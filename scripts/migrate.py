import asyncio
import logging
from pathlib import Path

import asyncpg

from app.config import settings

logger = logging.getLogger("app.migrate")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def run_migrations(pool: asyncpg.Pool) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)
    logger.info("migrations applied")


async def _main() -> None:
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=2, statement_cache_size=0)
    try:
        await run_migrations(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
