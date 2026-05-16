"""Knowledge retrieval engine — three-channel: fast KV + vector + BM25 keyword.

Channels:
  Fast:    exact / fuzzy match on entity_key (KV/FAQ) -> <50ms
  Vector:  FAISS semantic similarity search
  Keyword: jieba tokenization + SQL candidate fetch + BM25 scoring

Fusion: Reciprocal Rank Fusion (RRF, k=60) with configurable channel weights.
Reranker: Optional cross-encoder reranking (activated via runtime config).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import threading
import time
from sqlalchemy import select, or_, func, literal
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.models.knowledge import KnowledgeChunk, KnowledgeSource
from server.schemas.knowledge import RetrievalHit, RetrievalResponse

logger = logging.getLogger(__name__)

# ── Stopwords ────────────────────────────────────────────────────

_STOPWORDS_ZH = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
})

_STOPWORDS_EN = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall",
    "of", "in", "to", "for", "with", "on", "at", "by", "from",
    "as", "it", "that", "this", "and", "or", "but", "not", "so",
})

_STOPWORDS = _STOPWORDS_ZH | _STOPWORDS_EN

_CJK_TEXT_RE = re.compile(r"[\u4e00-\u9fff]+")


def _filter_tokens(tokens: list[str]) -> list[str]:
    """Normalize tokens and remove empty, single-character, and stopword terms."""
    filtered: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        term = token.strip().lower()
        if len(term) < 2 or term in _STOPWORDS or term in seen:
            continue
        filtered.append(term)
        seen.add(term)
    return filtered


def _cjk_ngrams(text: str) -> list[str]:
    """Generate fallback 2-4 char CJK n-grams when jieba returns one broad token."""
    tokens: list[str] = []
    for segment in _CJK_TEXT_RE.findall(text):
        max_n = min(4, len(segment))
        for n in range(2, max_n + 1):
            for idx in range(0, len(segment) - n + 1):
                tokens.append(segment[idx:idx + n])
    return tokens


# ── Tokenization ─────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Tokenize text using jieba (Chinese) + basic English splitting.

    Falls back to regex splitting if jieba is not installed.
    """
    try:
        import jieba
        tokens = jieba.lcut(text)
    except ImportError:
        # Fallback: split on punctuation/whitespace.
        tokens = re.split(r"[^\w\u4e00-\u9fff]+", text)

    filtered = _filter_tokens(tokens)
    if len(filtered) <= 1 and _CJK_TEXT_RE.search(text):
        filtered = _filter_tokens([*filtered, *_cjk_ngrams(text)])
    return filtered


# ── BM25 Scorer ──────────────────────────────────────────────────

def _bm25_score(
    query_tokens: list[str],
    doc_tokens_list: list[list[str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """Compute BM25 scores for a list of documents against a query.

    Returns a list of scores (one per document), normalized to [0, 1].
    """
    N = len(doc_tokens_list)
    if N == 0:
        return []

    # Average document length
    doc_lengths = [len(dt) for dt in doc_tokens_list]
    avgdl = sum(doc_lengths) / N if N > 0 else 1.0

    # Document frequency for each query token
    df: dict[str, int] = {}
    for qt in set(query_tokens):
        df[qt] = sum(1 for dt in doc_tokens_list if qt in dt)

    scores: list[float] = []
    for i, doc_tokens in enumerate(doc_tokens_list):
        dl = doc_lengths[i]
        score = 0.0
        # Count term frequencies in this doc
        tf_map: dict[str, int] = {}
        for t in doc_tokens:
            tf_map[t] = tf_map.get(t, 0) + 1

        for qt in set(query_tokens):
            if qt not in df or df[qt] == 0:
                continue
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            idf = math.log((N - df[qt] + 0.5) / (df[qt] + 0.5) + 1.0)
            # TF normalization with saturation
            tf_norm = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * dl / avgdl))
            score += idf * tf_norm

        scores.append(score)

    # Normalize to [0, 1]
    max_score = max(scores) if scores else 1.0
    if max_score > 0:
        scores = [s / max_score for s in scores]

    return scores


# ── RRF Fusion ───────────────────────────────────────────────────

def _rrf_fuse(
    hit_lists: list[list[RetrievalHit]],
    weights: list[float],
    k: int = 60,
    top_k: int = 10,
) -> list[RetrievalHit]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    score(d) = sum( weight_i / (k + rank_i) ) for each list containing d.
    Returns fused hits with channel="fused".
    """
    # chunk_id -> (rrf_score, best_hit)
    fused: dict[str, tuple[float, RetrievalHit]] = {}

    for hit_list, weight in zip(hit_lists, weights):
        for rank, hit in enumerate(hit_list):
            rrf_score = weight / (k + rank + 1)  # rank is 0-indexed, +1 for 1-indexed
            cid = hit.chunk_id
            if cid in fused:
                old_score, old_hit = fused[cid]
                fused[cid] = (old_score + rrf_score, old_hit)
            else:
                fused[cid] = (rrf_score, hit)

    # Sort by fused score descending
    sorted_items = sorted(fused.values(), key=lambda x: x[0], reverse=True)

    results = []
    for score, hit in sorted_items[:top_k]:
        results.append(RetrievalHit(
            chunk_id=hit.chunk_id,
            source_id=hit.source_id,
            source_name=hit.source_name,
            content=hit.content,
            score=round(score, 6),
            page=hit.page,
            paragraph=hit.paragraph,
            line_start=hit.line_start,
            line_end=hit.line_end,
            channel="fused",
        ))

    return results


# ── Cross-Encoder Reranker ───────────────────────────────────────

class Reranker:
    """Lazy-loaded cross-encoder reranker singleton."""

    _instance: Reranker | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._model = None

    @classmethod
    def get_instance(cls) -> Reranker | None:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = cls()
                    if inst._load():
                        cls._instance = inst
                    else:
                        return None
        return cls._instance

    def _load(self) -> bool:
        try:
            if settings.hf_endpoint:
                os.environ.setdefault("HF_ENDPOINT", settings.hf_endpoint)
            from sentence_transformers import CrossEncoder
            logger.info("Loading cross-encoder reranker: BAAI/bge-reranker-v2-m3")
            self._model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
            logger.info("Cross-encoder reranker loaded successfully")
            return True
        except ImportError:
            logger.warning("sentence-transformers not installed; reranker unavailable")
            return False
        except Exception as e:
            logger.warning(f"Failed to load cross-encoder reranker: {e}")
            return False

    def rerank(
        self, query: str, hits: list[RetrievalHit], top_k: int = 5,
    ) -> list[RetrievalHit]:
        """Rerank hits using cross-encoder scores."""
        if not hits or self._model is None:
            return hits[:top_k]

        pairs = [(query, hit.content) for hit in hits]
        try:
            scores = self._model.predict(pairs)
        except Exception as e:
            logger.warning(f"Reranker prediction failed: {e}")
            return hits[:top_k]

        # Pair scores with hits and sort
        scored = sorted(zip(scores, hits), key=lambda x: x[0], reverse=True)

        results = []
        for score, hit in scored[:top_k]:
            results.append(RetrievalHit(
                chunk_id=hit.chunk_id,
                source_id=hit.source_id,
                source_name=hit.source_name,
                content=hit.content,
                score=round(float(score), 6),
                page=hit.page,
                paragraph=hit.paragraph,
                line_start=hit.line_start,
                line_end=hit.line_end,
                channel=hit.channel,
            ))
        return results


# ── KnowledgeRetriever ───────────────────────────────────────────

class KnowledgeRetriever:
    """Three-channel retrieval with RRF fusion and optional reranking.

    Fast channel: exact / fuzzy match on entity_key (KV/FAQ) -> <50ms
    Vector channel: FAISS semantic similarity search
    Keyword channel: jieba tokenization + BM25 scoring
    Fusion: Reciprocal Rank Fusion (k=60, weighted)
    Reranker: Optional cross-encoder (activated via runtime config)
    """

    def __init__(self, db: AsyncSession, vector_store=None, runtime_cfg: dict | None = None):
        self.db = db
        self.vector_store = vector_store  # injected; None = skip vector search
        self._cfg = runtime_cfg or {}

    # ── Fast Channel ─────────────────────────────────────────────
    async def fast_lookup(self, query: str, domain: str | None = None, top_k: int = 3) -> list[RetrievalHit]:
        """Look up structured KV / FAQ entries by entity_key match."""
        stmt = (
            select(KnowledgeChunk, KnowledgeSource.name)
            .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
            .where(KnowledgeChunk.entity_key.isnot(None))
        )
        if domain:
            stmt = stmt.where(KnowledgeChunk.domain == domain)

        tokens = _tokenize(query)
        conditions = [
            KnowledgeChunk.entity_key.contains(query),
            KnowledgeChunk.content.contains(query),
            func.instr(literal(query), KnowledgeChunk.entity_key) > 0,
        ]
        for tok in tokens:
            conditions.append(KnowledgeChunk.entity_key.contains(tok))
            conditions.append(KnowledgeChunk.content.contains(tok))

        stmt = stmt.where(or_(*conditions)).limit(top_k)

        result = await self.db.execute(stmt)
        rows = result.all()

        hits = []
        for chunk, source_name in rows:
            hits.append(RetrievalHit(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                source_name=source_name,
                content=chunk.content,
                score=1.0,
                page=chunk.page_number,
                paragraph=chunk.paragraph_index,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                channel="fast",
            ))
        return hits

    # ── Vector Channel ───────────────────────────────────────────
    async def _vector_search(
        self, query: str, domain: str | None, top_k: int,
        ef_search: int = 128,
    ) -> list[RetrievalHit]:
        """Vector similarity search via FAISS."""
        if self.vector_store is None:
            return []

        try:
            results = self.vector_store.search(
                query, top_k=top_k, domain=domain,
                ef_search=ef_search,
            )
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

        if not results:
            return []

        chunk_ids = [r["chunk_id"] for r in results]
        score_map = {r["chunk_id"]: r["score"] for r in results}

        stmt = (
            select(KnowledgeChunk, KnowledgeSource.name)
            .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
            .where(KnowledgeChunk.id.in_(chunk_ids))
        )
        db_result = await self.db.execute(stmt)
        rows = {chunk.id: (chunk, source_name) for chunk, source_name in db_result.all()}

        hits = []
        for r in results:
            cid = r["chunk_id"]
            if cid not in rows:
                continue
            chunk, source_name = rows[cid]
            hits.append(RetrievalHit(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                source_name=source_name,
                content=chunk.content,
                score=score_map[cid],
                page=chunk.page_number,
                paragraph=chunk.paragraph_index,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                channel="vector",
            ))

        return hits

    # ── Keyword Channel (BM25) ───────────────────────────────────
    async def _keyword_search(
        self, query: str, domain: str | None, top_k: int,
    ) -> list[RetrievalHit]:
        """Two-phase BM25 keyword search: SQL candidate fetch + Python BM25 scoring."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # Phase 1: SQL LIKE candidate retrieval (over-fetch)
        conditions = [KnowledgeChunk.content.contains(query)]
        for tok in query_tokens:
            conditions.append(KnowledgeChunk.content.contains(tok))

        over_fetch = top_k * 5
        stmt = (
            select(KnowledgeChunk, KnowledgeSource.name)
            .join(KnowledgeSource, KnowledgeChunk.source_id == KnowledgeSource.id)
            .where(or_(*conditions))
        )
        if domain:
            stmt = stmt.where(KnowledgeChunk.domain == domain)
        stmt = stmt.limit(over_fetch)

        result = await self.db.execute(stmt)
        rows = result.all()

        if not rows:
            return []

        # Phase 2: BM25 scoring in Python
        doc_tokens_list = [_tokenize(chunk.content) for chunk, _ in rows]
        bm25_scores = _bm25_score(query_tokens, doc_tokens_list)

        # Pair with rows and sort by score
        scored = sorted(
            zip(bm25_scores, rows),
            key=lambda x: x[0],
            reverse=True,
        )

        hits = []
        for score, (chunk, source_name) in scored[:top_k]:
            if score <= 0:
                continue
            hits.append(RetrievalHit(
                chunk_id=chunk.id,
                source_id=chunk.source_id,
                source_name=source_name,
                content=chunk.content,
                score=round(score, 6),
                page=chunk.page_number,
                paragraph=chunk.paragraph_index,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                channel="keyword",
            ))

        return hits

    # ── Unified Retrieve ─────────────────────────────────────────
    async def retrieve(
        self, query: str, domain: str | None = None, top_k: int = 5,
        use_fast: bool = True, use_rag: bool = True,
    ) -> RetrievalResponse:
        """Three-channel retrieval with RRF fusion, optional reranking, and timeout.

        1. Fast channel runs first; if exact match (score=1.0), return immediately
        2. Vector + Keyword channels run concurrently via asyncio.gather
        3. RRF fusion merges results with configurable weights
        4. Optional cross-encoder reranking (if reranker_enabled in config)
        5. Fast hits + fused hits merged, deduplicated by chunk_id
        """
        # Read runtime config
        cfg_top_k = self._cfg.get("retrieval_top_k", top_k)
        top_k = min(top_k, cfg_top_k) if cfg_top_k else top_k
        keyword_weight = self._cfg.get("keyword_weight", 0.5)
        ef_search = self._cfg.get("ef_search", 128)
        reranker_enabled = self._cfg.get("reranker_enabled", False)
        timeout_ms = self._cfg.get("retrieval_timeout_ms", 10000)

        async def _inner() -> RetrievalResponse:
            t0 = time.perf_counter()
            all_hits: list[RetrievalHit] = []
            fast_answer: str | None = None

            # 1. Fast channel first
            if use_fast:
                try:
                    fast_hits = await self.fast_lookup(query, domain, top_k=3)
                except Exception as e:
                    logger.warning(f"Fast lookup failed: {e}")
                    fast_hits = []

                if fast_hits:
                    fast_answer = fast_hits[0].content
                    all_hits.extend(fast_hits)
                    # Exact match → return immediately
                    if any(h.score >= 1.0 for h in fast_hits):
                        return RetrievalResponse(
                            hits=all_hits[:top_k],
                            fast_answer=fast_answer,
                            query=query,
                            latency_ms=(time.perf_counter() - t0) * 1000,
                        )

            # 2. Vector + Keyword channels concurrently
            if use_rag:
                tasks = []
                task_names = []

                if self.vector_store is not None:
                    tasks.append(self._vector_search(
                        query, domain, top_k=top_k, ef_search=ef_search,
                    ))
                    task_names.append("vector")

                tasks.append(self._keyword_search(query, domain, top_k=top_k))
                task_names.append("keyword")

                results = await asyncio.gather(*tasks, return_exceptions=True)

                hit_lists: list[list[RetrievalHit]] = []
                weights: list[float] = []

                for name, result in zip(task_names, results):
                    if isinstance(result, Exception):
                        logger.warning(f"{name} channel failed: {result}")
                        continue
                    if result:
                        hit_lists.append(result)
                        weights.append(1.0 if name == "vector" else keyword_weight)

                # 3. RRF fusion
                if hit_lists:
                    fused_hits = _rrf_fuse(
                        hit_lists, weights,
                        k=60, top_k=top_k * 2,  # over-fetch for reranker
                    )

                    # 4. Optional cross-encoder reranking
                    if reranker_enabled and fused_hits:
                        reranker = Reranker.get_instance()
                        if reranker is not None:
                            fused_hits = reranker.rerank(
                                query, fused_hits, top_k=top_k,
                            )
                        else:
                            fused_hits = fused_hits[:top_k]
                    else:
                        fused_hits = fused_hits[:top_k]

                    # Merge fast + fused, deduplicate by chunk_id
                    seen_ids = {h.chunk_id for h in all_hits}
                    for h in fused_hits:
                        if h.chunk_id not in seen_ids:
                            all_hits.append(h)
                            seen_ids.add(h.chunk_id)

            # Sort by score descending
            all_hits.sort(key=lambda h: h.score, reverse=True)

            return RetrievalResponse(
                hits=all_hits[:top_k],
                fast_answer=fast_answer,
                query=query,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

        # Wrap with timeout
        try:
            return await asyncio.wait_for(_inner(), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            logger.warning(f"Retrieval timed out after {timeout_ms}ms for query: {query[:50]}")
            return RetrievalResponse(
                hits=[],
                fast_answer=None,
                query=query,
                latency_ms=float(timeout_ms),
            )
