"""pgvector-backed vector store adapter.

Uses PostgreSQL's pgvector extension for ACID-compliant vector storage.
Auto-selected when the database URL points to PostgreSQL.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from server.engine.vector_adapter import VectorStoreAdapter

logger = logging.getLogger(__name__)


class PgVectorStore(VectorStoreAdapter):
    """Vector store backed by PostgreSQL + pgvector extension."""

    def __init__(
        self,
        db_url: str,
        embedding_manager: Any,
        table_name: str = "vector_embeddings",
    ):
        self._db_url = db_url
        self._embedding = embedding_manager
        self._table_name = table_name
        self._dim = embedding_manager.dimension
        self._engine = None
        self._initialized = False
        self._init_store()

    def _init_store(self) -> None:
        """Initialize pgvector table and HNSW index."""
        try:
            from sqlalchemy import create_engine, text

            # Create sync engine from async URL (replace asyncpg with psycopg2)
            sync_url = self._db_url.replace("+asyncpg", "")
            self._engine = create_engine(sync_url)

            with self._engine.begin() as conn:
                # Enable pgvector extension
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

                # Create table if not exists
                conn.execute(text(
                    f"CREATE TABLE IF NOT EXISTS {self._table_name} ("
                    f"  id SERIAL PRIMARY KEY,"
                    f"  chunk_id VARCHAR(36) NOT NULL UNIQUE,"
                    f"  embedding vector({self._dim}) NOT NULL,"
                    f"  domain VARCHAR(64) DEFAULT 'default',"
                    f"  created_at TIMESTAMP DEFAULT NOW()"
                    f")"
                ))

                # Create HNSW index if not exists
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{self._table_name}_hnsw "
                    f"ON {self._table_name} "
                    f"USING hnsw (embedding vector_cosine_ops) "
                    f"WITH (m = 32, ef_construction = 200)"
                ))

                # Index on chunk_id for lookups
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{self._table_name}_chunk_id "
                    f"ON {self._table_name} (chunk_id)"
                ))

                # Index on domain for filtered searches
                conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_{self._table_name}_domain "
                    f"ON {self._table_name} (domain)"
                ))

            self._initialized = True
            logger.info(
                "pgvector store initialized: table=%s, dim=%d",
                self._table_name, self._dim,
            )
        except ImportError:
            logger.error("pgvector package not installed. Run: pip install pgvector")
            raise
        except Exception as e:
            logger.error("Failed to initialize pgvector store: %s", e)
            raise

    # ── Write operations ──────────────────────────────────

    def add(self, chunk_id: str, text: str, domain: str = "default") -> None:
        vec = self._embedding.encode([text], mode="document")[0]
        self._upsert_vector(chunk_id, vec, domain)

    def add_batch(self, items: list[dict[str, str]]) -> int:
        if not items:
            return 0
        texts = [item["text"] for item in items]
        vecs = self._embedding.encode(texts, mode="document")
        added = 0
        for item, vec in zip(items, vecs):
            try:
                self._upsert_vector(
                    item["chunk_id"], vec, item.get("domain", "default"),
                )
                added += 1
            except Exception as e:
                logger.warning(
                    "Failed to add vector for chunk %s: %s", item["chunk_id"], e,
                )
        return added

    def _upsert_vector(
        self, chunk_id: str, vec: np.ndarray, domain: str,
    ) -> None:
        from sqlalchemy import text

        vec_list = vec.astype(float).tolist()
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {self._table_name} (chunk_id, embedding, domain) "
                    f"VALUES (:chunk_id, :embedding, :domain) "
                    f"ON CONFLICT (chunk_id) "
                    f"DO UPDATE SET embedding = :embedding, domain = :domain"
                ),
                {
                    "chunk_id": chunk_id,
                    "embedding": str(vec_list),
                    "domain": domain,
                },
            )

    # ── Search ────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        domain: str | None = None,
        ef_search: int = 128,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        if allowed_chunk_ids == set():
            return []

        vec = self._embedding.encode([query], mode="query")[0]
        vec_list = vec.astype(float).tolist()

        with self._engine.begin() as conn:
            # Set ef_search for this transaction
            conn.execute(text(f"SET hnsw.ef_search = {int(ef_search)}"))

            conditions = []
            params = {"vec": str(vec_list), "top_k": top_k}
            if domain:
                conditions.append("domain = :domain")
                params["domain"] = domain
            if allowed_chunk_ids is not None:
                conditions.append("chunk_id = ANY(:allowed_ids)")
                params["allowed_ids"] = list(allowed_chunk_ids)
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            result = conn.execute(
                text(
                    f"SELECT chunk_id, domain, "
                    f"       1 - (embedding <=> CAST(:vec AS vector)) AS score "
                    f"FROM {self._table_name} {where} "
                    f"ORDER BY embedding <=> CAST(:vec AS vector) LIMIT :top_k"
                ),
                params,
            )

            return [
                {"chunk_id": row[0], "domain": row[1], "score": float(row[2])}
                for row in result.fetchall()
            ]

    # ── Delete ────────────────────────────────────────────

    def delete(self, chunk_ids: list[str]) -> int:
        if not chunk_ids:
            return 0
        from sqlalchemy import text

        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    f"DELETE FROM {self._table_name} "
                    f"WHERE chunk_id = ANY(:ids)"
                ),
                {"ids": chunk_ids},
            )
            return result.rowcount

    # ── Persistence (no-op) ───────────────────────────────

    def save(self) -> None:
        # pgvector persists automatically via PostgreSQL transactions
        pass

    # ── Stats ─────────────────────────────────────────────

    def count(self) -> int:
        from sqlalchemy import text

        with self._engine.begin() as conn:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {self._table_name}"),
            )
            return result.scalar() or 0

    @property
    def dimension(self) -> int:
        return self._dim
