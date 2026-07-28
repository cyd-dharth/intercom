import re
import uuid
from collections import OrderedDict

import asyncpg

from app.ai.client import embed_text
from app.repositories import kb as kb_repo

RRF_K = 60
TOP_ARTICLES = 3
QUERY_EMBEDDING_CACHE_SIZE = 256
MIN_QUERY_CHARS = 12
SNIPPET_CHARS = 220

_query_embedding_cache: "OrderedDict[str, list[float] | None]" = OrderedDict()


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


async def _cached_embed_query(query: str, workspace_id: uuid.UUID) -> list[float] | None:
    key = _normalize_query(query)
    if key in _query_embedding_cache:
        _query_embedding_cache.move_to_end(key)
        return _query_embedding_cache[key]
    embedding = await embed_text(query, workspace_id=workspace_id)
    _query_embedding_cache[key] = embedding
    _query_embedding_cache.move_to_end(key)
    if len(_query_embedding_cache) > QUERY_EMBEDDING_CACHE_SIZE:
        _query_embedding_cache.popitem(last=False)
    return embedding


def _snippet(content: str) -> str:
    if len(content) <= SNIPPET_CHARS:
        return content
    return content[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "..."


async def hybrid_search(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, query: str, limit: int = TOP_ARTICLES
) -> list[dict]:
    """Reciprocal rank fusion over vector and lexical search, collapsed to the best
    scoring chunk per article, per CLAUDE.md section 10.3. Degrades silently to lexical
    only if embedding fails (no LLM, budget exceeded, or provider error) so the widget
    suggestion panel never blocks on AI availability."""
    query = query.strip()
    if len(query) < MIN_QUERY_CHARS:
        return []

    query_embedding = await _cached_embed_query(query, workspace_id)

    vector_rows = await kb_repo.vector_search(conn, workspace_id, query_embedding, limit=20) if query_embedding else []
    lexical_rows = await kb_repo.lexical_search(conn, workspace_id, query, limit=20)

    scores: dict[uuid.UUID, float] = {}
    best_chunk: dict[uuid.UUID, asyncpg.Record] = {}

    for row in vector_rows:
        article_id = row["article_id"]
        scores[article_id] = scores.get(article_id, 0.0) + 1.0 / (RRF_K + row["rank"])
        if article_id not in best_chunk:
            best_chunk[article_id] = row

    for row in lexical_rows:
        article_id = row["article_id"]
        scores[article_id] = scores.get(article_id, 0.0) + 1.0 / (RRF_K + row["rank"])
        if article_id not in best_chunk:
            best_chunk[article_id] = row

    ranked_article_ids = sorted(scores.keys(), key=lambda a: scores[a], reverse=True)[:limit]
    if not ranked_article_ids:
        return []

    articles = await kb_repo.get_articles_by_ids(conn, workspace_id, ranked_article_ids)
    articles_by_id = {a["id"]: a for a in articles}

    results = []
    for article_id in ranked_article_ids:
        article = articles_by_id.get(article_id)
        if article is None or article["status"] != "published":
            continue
        chunk = best_chunk[article_id]
        results.append(
            {
                "article_id": str(article_id),
                "title": article["title"],
                "slug": article["slug"],
                "snippet": _snippet(chunk["content"]),
            }
        )
    return results
