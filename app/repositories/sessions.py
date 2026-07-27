import uuid
from datetime import datetime

import asyncpg


async def create_session(conn: asyncpg.Connection, user_id: uuid.UUID, token_hash: str, expires_at: datetime) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into sessions (user_id, token_hash, expires_at)
        values ($1, $2, $3)
        returning id, user_id, expires_at, created_at
        """,
        user_id,
        token_hash,
        expires_at,
    )


async def get_session_by_token_hash(conn: asyncpg.Connection, token_hash: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, user_id, expires_at
          from sessions
         where token_hash = $1 and expires_at > now()
        """,
        token_hash,
    )


async def delete_session(conn: asyncpg.Connection, token_hash: str) -> None:
    await conn.execute("delete from sessions where token_hash = $1", token_hash)
