"""
ChromaDB vector store service.
Persists chat history as embeddings and supports semantic retrieval.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings

logger = logging.getLogger(__name__)


class VectorStoreService:
    """Thin wrapper around ChromaDB for chat-history storage and retrieval."""

    def __init__(self) -> None:
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        settings = get_settings()
        self._client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB connected — collection '%s'.", settings.chroma_collection)

    # ── write ─────────────────────────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """Store a single chat message with its session metadata."""
        doc_id = str(uuid.uuid4())
        self._collection.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[{"session_id": session_id, "role": role}],
        )

    # ── read ──────────────────────────────────────────────────────────────────

    def get_session_history(self, session_id: str, limit: int = 20) -> List[dict]:
        """Return the most-recent *limit* messages for a session (ordered by add time)."""
        result = self._collection.get(
            where={"session_id": session_id},
            include=["documents", "metadatas"],
        )
        messages = [
            {"role": meta["role"], "content": doc}
            for meta, doc in zip(result["metadatas"], result["documents"])
        ]
        return messages[-limit:]

    def semantic_search(
        self,
        query: str,
        session_id: Optional[str] = None,
        n_results: int = 3,
    ) -> List[str]:
        """
        Retrieve semantically similar past messages.
        Optionally filter to a specific session.
        """
        where = {"session_id": session_id} if session_id else None
        result = self._collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
            include=["documents"],
        )
        return result["documents"][0] if result["documents"] else []

    def clear_session(self, session_id: str) -> None:
        """Remove all stored messages for a session."""
        result = self._collection.get(where={"session_id": session_id})
        if result["ids"]:
            self._collection.delete(ids=result["ids"])


# Singleton
vector_store = VectorStoreService()
