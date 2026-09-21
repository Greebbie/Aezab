"""Abstract vector store adapter interface.

All vector store backends (FAISS, pgvector, Milvus) must implement this
interface so the retrieval pipeline can work with any backend transparently.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VectorStoreAdapter(ABC):
    """Abstract interface for vector storage backends."""

    @abstractmethod
    def add(self, chunk_id: str, text: str, domain: str = "default") -> None:
        """Embed a single text and add to the index."""

    @abstractmethod
    def add_batch(self, items: list[dict[str, str]]) -> int:
        """Add multiple items. Each item: {chunk_id, text, domain}. Returns count added."""

    @abstractmethod
    def search(
        self, query: str, top_k: int = 5, domain: str | None = None,
        ef_search: int = 128,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search within allowed chunk IDs; an empty set returns no results."""

    @abstractmethod
    def delete(self, chunk_ids: list[str]) -> int:
        """Delete vectors by chunk IDs. Returns count deleted."""

    @abstractmethod
    def save(self) -> None:
        """Persist index to storage (no-op for DB-backed stores)."""

    @abstractmethod
    def count(self) -> int:
        """Return total number of vectors in the store."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""
