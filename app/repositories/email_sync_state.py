import asyncpg


async def get_last_uid(conn: asyncpg.Connection) -> int:
    row = await conn.fetchrow("select last_uid from email_sync_state where id = 1")
    return row["last_uid"] if row else 0


async def set_last_uid(conn: asyncpg.Connection, last_uid: int) -> None:
    await conn.execute(
        "update email_sync_state set last_uid = $1, updated_at = now() where id = 1",
        last_uid,
    )
