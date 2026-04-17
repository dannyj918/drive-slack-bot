"""
RAG retriever
=============
Query-time interface for the vector knowledge base.

At query time, the user's question is embedded with the same OpenAI model used
during indexing, then Chroma returns the most semantically similar text chunks
from the pre-indexed Shared Drive documents.

Returns [] gracefully if the index hasn't been built yet (run rag_indexer.py first).
"""

import logging
import os

import chromadb
import openai

logger = logging.getLogger(__name__)

_openai_client: openai.OpenAI | None = None

EMBEDDING_MODEL = "text-embedding-3-small"
COLLECTION_NAME = "drive_docs"


def _get_openai_client() -> openai.OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


def _get_collection() -> chromadb.Collection | None:
    """
    Open a fresh Chroma client on every call so queries always see the latest
    index written by rag_indexer.py without requiring a bot restart.
    """
    db_path = os.environ.get("CHROMA_DB_PATH", "./chroma_db")
    if not os.path.exists(db_path):
        logger.warning(
            "Chroma DB not found at %s — run rag_indexer.py to build the index", db_path
        )
        return None

    try:
        client = chromadb.PersistentClient(path=db_path)
        collection = client.get_collection(COLLECTION_NAME)
        logger.info("RAG collection loaded: %d chunks", collection.count())
        return collection
    except Exception as exc:
        logger.warning("Could not load RAG collection: %s", exc)
        return None


def search(query: str, n_results: int = 5) -> list[dict]:
    """
    Semantic search over the indexed Shared Drive content.

    Parameters
    ----------
    query     : Natural language question or keyword string.
    n_results : Maximum number of content chunks to return.

    Returns
    -------
    List of dicts with keys: text, file_name, file_link.
    Returns [] if the index is empty or not yet built.
    """
    collection = _get_collection()
    if collection is None:
        return []

    count = collection.count()
    if count == 0:
        return []

    try:
        oai = _get_openai_client()
        response = oai.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[query],
        )
        query_embedding = response.data[0].embedding

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, count),
            include=["documents", "metadatas"],
        )

        chunks = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        for doc, meta in zip(docs, metas):
            chunks.append(
                {
                    "text":      doc,
                    "file_name": meta.get("file_name", ""),
                    "file_link": meta.get("file_link", ""),
                }
            )

        logger.info("RAG search %r → %d chunk(s)", query, len(chunks))
        return chunks

    except Exception as exc:
        logger.error("RAG retriever error: %s", exc)
        return []
