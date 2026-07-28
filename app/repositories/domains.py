import uuid

import asyncpg


async def create_domain(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, hostname: str, verification_token: str
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into custom_domains (workspace_id, hostname, verification_token)
        values ($1, $2, $3)
        returning id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
        """,
        workspace_id,
        hostname,
        verification_token,
    )


async def list_domains(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
          from custom_domains
         where workspace_id = $1
         order by created_at desc
        """,
        workspace_id,
    )


async def get_by_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, domain_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
          from custom_domains
         where workspace_id = $1 and id = $2
        """,
        workspace_id,
        domain_id,
    )


async def hostname_exists(conn: asyncpg.Connection, hostname: str) -> bool:
    row = await conn.fetchrow("select 1 from custom_domains where hostname = $1", hostname)
    return row is not None


async def get_verified_by_hostname(conn: asyncpg.Connection, hostname: str) -> asyncpg.Record | None:
    """Unscoped by workspace_id, unlike every other query in this module. Used only by
    the public Host header routing middleware, where the workspace is not yet known and
    is exactly what this lookup resolves. Only ever returns a verified row, so an
    unverified or spoofed Host header cannot be used to reach another tenant's KB."""
    return await conn.fetchrow(
        """
        select id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
          from custom_domains
         where hostname = $1 and status = 'verified'
        """,
        hostname,
    )


async def mark_verified(conn: asyncpg.Connection, domain_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update custom_domains
           set status = 'verified', last_checked_at = now(), last_error = null
         where id = $1
        returning id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
        """,
        domain_id,
    )


async def mark_failed(conn: asyncpg.Connection, domain_id: uuid.UUID, error: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update custom_domains
           set status = 'failed', last_checked_at = now(), last_error = $2
         where id = $1
        returning id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
        """,
        domain_id,
        error[:2000],
    )


async def mark_pending_retry(conn: asyncpg.Connection, domain_id: uuid.UUID, error: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update custom_domains
           set last_checked_at = now(), last_error = $2
         where id = $1
        returning id, workspace_id, hostname, verification_token, status, last_checked_at, last_error, created_at
        """,
        domain_id,
        error[:2000],
    )
