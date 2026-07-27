import uuid

import asyncpg


async def get_user_by_email(conn: asyncpg.Connection, email: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, email, password_hash, name, created_at
          from users
         where lower(email) = lower($1)
        """,
        email,
    )


async def get_user_by_id(conn: asyncpg.Connection, user_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, email, name, created_at
          from users
         where id = $1
        """,
        user_id,
    )


async def create_user(conn: asyncpg.Connection, email: str, password_hash: str, name: str) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into users (email, password_hash, name)
        values ($1, $2, $3)
        returning id, email, name, created_at
        """,
        email,
        password_hash,
        name,
    )
