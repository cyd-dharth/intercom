import uuid

import asyncpg


async def get_by_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, contact_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, visitor_id, email, name, last_seen_at, metadata, created_at
          from contacts
         where workspace_id = $1 and id = $2
        """,
        workspace_id,
        contact_id,
    )


async def get_by_visitor_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, visitor_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, visitor_id, email, name, last_seen_at, metadata, created_at
          from contacts
         where workspace_id = $1 and visitor_id = $2
        """,
        workspace_id,
        visitor_id,
    )


async def get_by_email(conn: asyncpg.Connection, workspace_id: uuid.UUID, email: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, visitor_id, email, name, last_seen_at, metadata, created_at
          from contacts
         where workspace_id = $1 and lower(email) = lower($2)
        """,
        workspace_id,
        email,
    )


async def create_visitor_contact(conn: asyncpg.Connection, workspace_id: uuid.UUID, visitor_id: str) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into contacts (workspace_id, visitor_id, last_seen_at)
        values ($1, $2, now())
        on conflict (workspace_id, visitor_id) where visitor_id is not null
        do update set last_seen_at = now()
        returning id, workspace_id, visitor_id, email, name, last_seen_at, metadata, created_at
        """,
        workspace_id,
        visitor_id,
    )


async def create_email_contact(conn: asyncpg.Connection, workspace_id: uuid.UUID, email: str, name: str | None) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into contacts (workspace_id, email, name, last_seen_at)
        values ($1, $2, $3, now())
        on conflict (workspace_id, lower(email)) where email is not null
        do update set last_seen_at = now()
        returning id, workspace_id, visitor_id, email, name, last_seen_at, metadata, created_at
        """,
        workspace_id,
        email,
        name,
    )


async def get_reply_address(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> str | None:
    row = await conn.fetchrow(
        """
        select ct.email
          from conversations c
          join contacts ct on ct.id = c.contact_id
         where c.workspace_id = $1 and c.id = $2
        """,
        workspace_id,
        conversation_id,
    )
    return row["email"] if row else None


async def touch_last_seen(conn: asyncpg.Connection, workspace_id: uuid.UUID, contact_id: uuid.UUID) -> None:
    await conn.execute(
        """
        update contacts set last_seen_at = now()
         where workspace_id = $1 and id = $2
        """,
        workspace_id,
        contact_id,
    )
