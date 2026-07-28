import uuid

import asyncpg


async def get_last_read_seq(conn: asyncpg.Connection, conversation_id: uuid.UUID, participant: str) -> int:
    row = await conn.fetchrow(
        """
        select last_read_seq from read_state
         where conversation_id = $1 and participant = $2
        """,
        conversation_id,
        participant,
    )
    return row["last_read_seq"] if row else 0


async def set_last_read_seq(conn: asyncpg.Connection, conversation_id: uuid.UUID, participant: str, seq: int) -> None:
    await conn.execute(
        """
        insert into read_state (conversation_id, participant, last_read_seq, updated_at)
        values ($1, $2, $3, now())
        on conflict (conversation_id, participant)
        do update set last_read_seq = greatest(read_state.last_read_seq, excluded.last_read_seq), updated_at = now()
        """,
        conversation_id,
        participant,
        seq,
    )
