import time
import uuid

import asyncpg

from app.repositories import ai as ai_repo

_CACHE_TTL_SECONDS = 30
_cache: dict[uuid.UUID, tuple[int, float]] = {}


async def get_spent_cents_cached(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> int:
    now = time.monotonic()
    cached = _cache.get(workspace_id)
    if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
        return cached[0]
    spent_micros = await ai_repo.sum_cost_today(conn, workspace_id)
    spent_cents = spent_micros // 10_000
    _cache[workspace_id] = (spent_cents, now)
    return spent_cents


async def is_budget_exceeded(conn: asyncpg.Connection, workspace_id: uuid.UUID, daily_budget_cents: int) -> bool:
    spent_cents = await get_spent_cents_cached(conn, workspace_id)
    return spent_cents >= daily_budget_cents
