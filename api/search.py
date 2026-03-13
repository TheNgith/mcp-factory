"""
api/search.py — Azure AI Search semantic tool selection (P5)

Embeds all invocables at generate time using text-embedding-3-small and
stores them in Azure AI Search.  At chat time, embed the user query and
vector-search top-k (default 15) to keep tool lists inside the GPT-4o
128-tool context window.

Falls back gracefully to cosine-dot-product in memory when Azure AI Search
is not configured (AZURE_SEARCH_ENDPOINT unset).

Environment variables:
    AZURE_SEARCH_ENDPOINT   https://mcp-factory-search.search.windows.net
    AZURE_SEARCH_ADMIN_KEY  (optional; uses Managed Identity if absent)
    AZURE_CLIENT_ID         Managed Identity client ID (set by ACA)
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from typing import Any

logger = logging.getLogger("mcp_factory.search")

SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_ADMIN_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "")
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_DIMS = 1536  # dimensions for text-embedding-3-small

# ── In-memory fallback cache (keyed by job_id) ─────────────────────────────
# {job_id: {"functions": [...openai tool schema...], "embeddings": [[float...]]}}
_EMBED_CACHE: dict[str, dict] = {}
_EMBED_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(v: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v] if mag else v


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _index_name(job_id: str) -> str:
    """Azure AI Search index name for a job (must be lowercase alphanumeric + hyphens)."""
    return f"mcp-{job_id.lower()}"


def _get_search_client(job_id: str):
    """Return an Azure Search SearchClient; None if SDK / endpoint not available."""
    if not SEARCH_ENDPOINT:
        return None
    try:
        from azure.search.documents import SearchClient  # type: ignore
        from azure.search.documents.indexes import SearchIndexClient  # type: ignore
        from azure.core.credentials import AzureKeyCredential  # type: ignore
        from azure.identity import ManagedIdentityCredential, DefaultAzureCredential  # type: ignore

        if SEARCH_ADMIN_KEY:
            cred = AzureKeyCredential(SEARCH_ADMIN_KEY)
        else:
            client_id = os.getenv("AZURE_CLIENT_ID", "")
            cred = (
                ManagedIdentityCredential(client_id=client_id)
                if client_id
                else DefaultAzureCredential()
            )
        return SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=_index_name(job_id),
            credential=cred,
        )
    except ImportError:
        return None


def _get_index_admin_client():
    """Return an Azure Search SearchIndexClient; None if unavailable."""
    if not SEARCH_ENDPOINT:
        return None
    try:
        from azure.search.documents.indexes import SearchIndexClient  # type: ignore
        from azure.core.credentials import AzureKeyCredential  # type: ignore
        from azure.identity import ManagedIdentityCredential, DefaultAzureCredential  # type: ignore

        if SEARCH_ADMIN_KEY:
            cred = AzureKeyCredential(SEARCH_ADMIN_KEY)
        else:
            client_id = os.getenv("AZURE_CLIENT_ID", "")
            cred = (
                ManagedIdentityCredential(client_id=client_id)
                if client_id
                else DefaultAzureCredential()
            )
        return SearchIndexClient(endpoint=SEARCH_ENDPOINT, credential=cred)
    except ImportError:
        return None


def _ensure_index(job_id: str) -> None:
    """Create the Azure AI Search vector index for job_id if it doesn't exist."""
    idx_client = _get_index_admin_client()
    if idx_client is None:
        return

    from azure.search.documents.indexes.models import (  # type: ignore
        SearchIndex,
        SearchField,
        SearchFieldDataType,
        SimpleField,
        SearchableField,
        VectorSearch,
        HnswAlgorithmConfiguration,
        VectorSearchProfile,
        SearchField as VectorField,
    )

    name = _index_name(job_id)
    try:
        idx_client.get_index(name)
        return  # already exists
    except Exception:
        pass  # create it

    index = SearchIndex(
        name=name,
        fields=[
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="tool_name", type=SearchFieldDataType.String),
            SearchableField(name="description", type=SearchFieldDataType.String),
            SimpleField(name="invocable_json", type=SearchFieldDataType.String),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=VECTOR_DIMS,
                vector_search_profile_name="hnsw-profile",
            ),
        ],
        vector_search=VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
            profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
        ),
    )
    try:
        idx_client.create_index(index)
        logger.info("[search] Created AI Search index: %s", name)
    except Exception as exc:
        logger.warning("[search] Could not create index %s: %s", name, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_and_index(
    job_id: str,
    invocables: list[dict],
    openai_client: Any,
    functions: list[dict] | None = None,
) -> None:
    """Embed all invocables and store in Azure AI Search (or in-memory fallback).

    Args:
        job_id:        Identifies the job / index.
        invocables:    List of invocable dicts (from discovery pipeline).
        openai_client: An openai.AzureOpenAI (or openai.OpenAI) client.
        functions:     Pre-built OpenAI function-calling schemas (optional).
                       If None, they are built from invocables.
    """
    if not invocables:
        return

    # Build function schemas if not provided
    if functions is None:
        functions = _build_functions(invocables)

    texts = [
        f"{fn['function']['name']}: {fn['function']['description']}"
        for fn in functions
    ]

    # Embed all tool descriptions
    try:
        resp = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
        )
        embeddings = [
            _norm(item.embedding)
            for item in sorted(resp.data, key=lambda x: x.index)
        ]
    except Exception as exc:
        logger.warning("[search] Embedding failed: %s", exc)
        return

    # ── Store in Azure AI Search ────────────────────────────────────────────
    if SEARCH_ENDPOINT:
        try:
            _ensure_index(job_id)
            sc = _get_search_client(job_id)
            if sc:
                docs = [
                    {
                        "id": f"{job_id}_{i}",
                        "tool_name": functions[i]["function"]["name"],
                        "description": functions[i]["function"]["description"],
                        "invocable_json": json.dumps(invocables[i]),
                        "embedding": embeddings[i],
                    }
                    for i in range(len(functions))
                ]
                sc.upload_documents(documents=docs)
                logger.info(
                    "[search] Indexed %d tools in Azure AI Search for job %s",
                    len(docs), job_id,
                )
        except Exception as exc:
            logger.warning("[search] AI Search indexing failed, using memory fallback: %s", exc)

    # ── Always cache in memory (cheap fallback + fast retrieval) ───────────
    with _EMBED_LOCK:
        _EMBED_CACHE[job_id] = {
            "functions": functions,
            "embeddings": embeddings,
            "invocables": invocables,
        }

    # ── Persist embeddings to blob so other ACA replicas can warm up ──────
    # Written to {job_id}/embeddings_cache.json; loaded by retrieve_tools
    # when the in-memory cache is cold (different replica handled generate).
    try:
        from api.storage import _upload_to_blob  # type: ignore
        from api.config import ARTIFACT_CONTAINER  # type: ignore
        _cache_blob = json.dumps({"functions": functions, "embeddings": embeddings})
        _upload_to_blob(
            ARTIFACT_CONTAINER,
            f"{job_id}/embeddings_cache.json",
            _cache_blob.encode(),
        )
        logger.info("[search] Embeddings persisted to blob for job %s", job_id)
    except Exception as _blob_exc:
        logger.warning(
            "[search] Embedding blob persist failed (non-fatal): %s", _blob_exc
        )


def retrieve_tools(
    job_id: str,
    query: str,
    openai_client: Any,
    top_k: int = 15,
) -> list[dict]:
    """Return top-k OpenAI function-calling schemas most relevant to query.

    Tries Azure AI Search first; falls back to in-memory cosine search.
    Returns the full functions list if the job has ≤ top_k tools.

    Returns:
        List of OpenAI function-calling tool schema dicts.
    """
    # Cache hit + small enough set → skip retrieval
    with _EMBED_LOCK:
        cached = _EMBED_CACHE.get(job_id)

    if cached and len(cached.get("functions", [])) <= top_k:
        return cached["functions"]

    # Embed the query
    try:
        q_resp = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[query],
        )
        q_vec = _norm(q_resp.data[0].embedding)
    except Exception as exc:
        logger.warning("[search] Query embedding failed: %s", exc)
        if cached:
            return cached["functions"][:min(top_k, 128)]
        return []

    # ── Try Azure AI Search vector search ──────────────────────────────────
    if SEARCH_ENDPOINT:
        try:
            sc = _get_search_client(job_id)
            if sc:
                from azure.search.documents.models import VectorizedQuery  # type: ignore
                results = sc.search(
                    search_text=None,
                    vector_queries=[
                        VectorizedQuery(
                            vector=q_vec,
                            k_nearest_neighbors=top_k,
                            fields="embedding",
                        )
                    ],
                    select=["tool_name", "description", "invocable_json"],
                    top=top_k,
                )
                matched: list[dict] = []
                for r in results:
                    try:
                        inv = json.loads(r["invocable_json"])
                        matched.append(_invocable_to_function(inv))
                    except Exception:
                        pass
                if matched:
                    logger.info(
                        "[search] AI Search returned %d tools for job %s", len(matched), job_id
                    )
                    return matched
        except Exception as exc:
            logger.warning("[search] AI Search query failed, using memory: %s", exc)
    # ── Blob cache warm-up (cross-replica cache miss recovery) ────────────
    # If generate ran on a different ACA replica the in-memory cache here is
    # cold.  Load the persisted embeddings from blob and warm this replica
    # so the cosine search below works, and the next call is instant.
    if not cached:
        try:
            from api.storage import _download_blob  # type: ignore
            from api.config import ARTIFACT_CONTAINER  # type: ignore
            _raw = _download_blob(ARTIFACT_CONTAINER, f"{job_id}/embeddings_cache.json")
            _data = json.loads(_raw)
            with _EMBED_LOCK:
                _EMBED_CACHE[job_id] = {
                    "functions": _data["functions"],
                    "embeddings": _data["embeddings"],
                    "invocables": [],
                }
                cached = _EMBED_CACHE[job_id]
            logger.info("[search] Warmed embedding cache from blob for job %s", job_id)
        except Exception as _warm_exc:
            logger.debug(
                "[search] Blob cache warm-up failed for job %s: %s", job_id, _warm_exc
            )
    # ── In-memory cosine fallback ──────────────────────────────────────────
    if cached:
        embs = cached["embeddings"]
        fns  = cached["functions"]
        scores = [(_dot(q_vec, e), i) for i, e in enumerate(embs)]
        scores.sort(reverse=True)
        return [fns[i] for _, i in scores[:top_k]]

    return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_functions(invocables: list[dict]) -> list[dict]:
    """Convert invocable dicts to OpenAI function-calling schemas."""
    _C_TO_JSON: dict[str, str] = {
        "int": "integer", "unsigned": "integer", "unsigned int": "integer",
        "long": "integer", "unsigned long": "integer", "size_t": "integer",
        "float": "number", "double": "number", "bool": "boolean",
    }

    def _json_type(c: str) -> str:
        t = c.lower().strip().rstrip("*").strip()
        if t in _C_TO_JSON:
            return _C_TO_JSON[t]
        if "char" in t or "string" in t or t == "str":
            return "string"
        if "int" in t or "long" in t:
            return "integer"
        return "string"

    fns = []
    for inv in invocables:
        props: dict = {}
        required: list = []
        for p in inv.get("parameters", []):
            pname = p.get("name", "arg")
            props[pname] = {
                "type": _json_type(p.get("type", "string")),
                "description": p.get("description", p.get("type", "")),
            }
        desc = (
            inv.get("doc")
            or inv.get("description")
            or inv.get("signature")
            or inv["name"]
        )
        fns.append({
            "type": "function",
            "function": {
                "name": inv["name"],
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return fns


def _invocable_to_function(inv: dict) -> dict:
    """Convert a single invocable dict to an OpenAI function-calling schema."""
    return _build_functions([inv])[0]
