import contextlib
import logging

import asyncpg

from app.config import settings

logger = logging.getLogger("app.db")

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
        statement_cache_size=0,
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
