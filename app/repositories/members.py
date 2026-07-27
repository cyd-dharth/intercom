import uuid

import asyncpg


async def add_member(conn: asyncpg.Connection, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
    await conn.execute(
        """
        insert into workspace_members (workspace_id, user_id, role)
        values ($1, $2, $3)
        on conflict (workspace_id, user_id) do update set role = excluded.role
        """,
        workspace_id,
        user_id,
        role,
    )


async def get_membership(conn: asyncpg.Connection, workspace_id: uuid.UUID, user_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select workspace_id, user_id, role, created_at
          from workspace_members
         where workspace_id = $1 and user_id = $2
        """,
        workspace_id,
        user_id,
    )


async def list_members(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select u.id, u.email, u.name, wm.role, wm.created_at
          from workspace_members wm
          join users u on u.id = wm.user_id
         where wm.workspace_id = $1
         order by wm.created_at asc
        """,
        workspace_id,
    )


async def list_memberships_for_user(conn: asyncpg.Connection, user_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select w.id, w.name, w.slug, wm.role
          from workspace_members wm
          join workspaces w on w.id = wm.workspace_id
         where wm.user_id = $1
         order by wm.created_at asc
        """,
        user_id,
    )


async def update_role(conn: asyncpg.Connection, workspace_id: uuid.UUID, user_id: uuid.UUID, role: str) -> None:
    await conn.execute(
        """
        update workspace_members
           set role = $3
         where workspace_id = $1 and user_id = $2
        """,
        workspace_id,
        user_id,
        role,
    )


async def count_admins(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> int:
    row = await conn.fetchrow(
        """
        select count(*) as c from workspace_members
         where workspace_id = $1 and role = 'admin'
        """,
        workspace_id,
    )
    return row["c"]
