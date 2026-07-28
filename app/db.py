import contextlib
import json
import logging

import asyncpg
from pgvector.asyncpg import register_vector

from app.config import settings

logger = logging.getLogger("app.db")

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Registers the pgvector codec (so kb_chunks.embedding round trips as a plain
    list[float], never a hand formatted string) and a jsonb codec (so dict/list values
    passed as query params are encoded automatically, no repository needs json.dumps).
    Runs once per pooled connection via asyncpg's init hook."""
    await register_vector(conn)
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda value: json.dumps(value),
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
        statement_cache_size=0,
        init=_init_connection,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


@contextlib.asynccontextmanager
async def transaction():
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
