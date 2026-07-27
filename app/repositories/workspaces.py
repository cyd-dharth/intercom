import secrets
import uuid

import asyncpg


async def create_workspace(conn: asyncpg.Connection, name: str, slug: str) -> asyncpg.Record:
    public_key = f"pk_{secrets.token_urlsafe(16)}"
    return await conn.fetchrow(
        """
        insert into workspaces (name, slug, public_key)
        values ($1, $2, $3)
        returning id, name, slug, public_key, allowed_origins, ai_daily_budget_cents, created_at
        """,
        name,
        slug,
        public_key,
    )


async def get_workspace_by_id(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, name, slug, public_key, allowed_origins, ai_daily_budget_cents, created_at
          from workspaces
         where id = $1
        """,
        workspace_id,
    )


async def get_workspace_by_slug(conn: asyncpg.Connection, slug: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, name, slug, public_key, allowed_origins, ai_daily_budget_cents, created_at
          from workspaces
         where slug = $1
        """,
        slug,
    )


async def get_workspace_by_public_key(conn: asyncpg.Connection, public_key: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, name, slug, public_key, allowed_origins, ai_daily_budget_cents, created_at
          from workspaces
         where public_key = $1
        """,
        public_key,
    )


async def slug_exists(conn: asyncpg.Connection, slug: str) -> bool:
    row = await conn.fetchrow("select 1 from workspaces where slug = $1", slug)
    return row is not None
