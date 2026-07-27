import uuid
from datetime import datetime

import asyncpg


async def create_invite(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    email: str,
    role: str,
    token_hash: str,
    invited_by: uuid.UUID,
    expires_at: datetime,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into invites (workspace_id, email, role, token_hash, invited_by, expires_at)
        values ($1, $2, $3, $4, $5, $6)
        returning id, workspace_id, email, role, expires_at, created_at
        """,
        workspace_id,
        email,
        role,
        token_hash,
        invited_by,
        expires_at,
    )


async def get_invite_by_token_hash(conn: asyncpg.Connection, token_hash: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, email, role, invited_by, accepted_at, expires_at, created_at
          from invites
         where token_hash = $1
        """,
        token_hash,
    )


async def mark_accepted(conn: asyncpg.Connection, invite_id: uuid.UUID) -> None:
    await conn.execute(
        "update invites set accepted_at = now() where id = $1",
        invite_id,
    )


async def list_pending_invites(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, email, role, expires_at, created_at
          from invites
         where workspace_id = $1 and accepted_at is null
         order by created_at desc
        """,
        workspace_id,
    )
