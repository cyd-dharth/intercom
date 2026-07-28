import uuid

import asyncpg


async def get_by_client_msg_id(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID, client_msg_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
               client_msg_id, email_message_id, email_in_reply_to, created_at
          from messages
         where workspace_id = $1 and conversation_id = $2 and client_msg_id = $3
        """,
        workspace_id,
        conversation_id,
        client_msg_id,
    )


async def insert_message(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seq: int,
    sender_type: str,
    body: str,
    sender_user_id: uuid.UUID | None = None,
    client_msg_id: str | None = None,
    email_message_id: str | None = None,
    email_in_reply_to: str | None = None,
    message_id: uuid.UUID | None = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into messages (
            id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
            client_msg_id, email_message_id, email_in_reply_to
        )
        values (coalesce($10, gen_random_uuid()), $1, $2, $3, $4, $5, $6, $7, $8, $9)
        returning id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
                  client_msg_id, email_message_id, email_in_reply_to, created_at
        """,
        workspace_id,
        conversation_id,
        seq,
        sender_type,
        sender_user_id,
        body,
        client_msg_id,
        email_message_id,
        email_in_reply_to,
        message_id,
    )


async def get_by_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, message_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
               client_msg_id, email_message_id, email_in_reply_to, created_at
          from messages
         where workspace_id = $1 and id = $2
        """,
        workspace_id,
        message_id,
    )


async def get_by_email_message_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, email_message_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
               client_msg_id, email_message_id, email_in_reply_to, created_at
          from messages
         where workspace_id = $1 and email_message_id = $2
        """,
        workspace_id,
        email_message_id,
    )


async def find_by_email_message_id_any_workspace(conn: asyncpg.Connection, email_message_id: str) -> asyncpg.Record | None:
    """Unscoped by workspace_id, unlike every other query in this module. Used only to
    resolve inbound email threading before the workspace is known: email_message_id is
    globally unique (it is our own generated Message-ID from an earlier outbound reply,
    or the sender's own Message-ID), so this is safe to look up directly."""
    return await conn.fetchrow(
        """
        select id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
               client_msg_id, email_message_id, email_in_reply_to, created_at
          from messages
         where email_message_id = $1
        """,
        email_message_id,
    )


async def list_since_seq(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID, since_seq: int
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
               client_msg_id, email_message_id, email_in_reply_to, created_at
          from messages
         where workspace_id = $1 and conversation_id = $2 and seq > $3
         order by seq asc
        """,
        workspace_id,
        conversation_id,
        since_seq,
    )


async def list_recent(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID, limit: int = 50
) -> list[asyncpg.Record]:
    rows = await conn.fetch(
        """
        select id, workspace_id, conversation_id, seq, sender_type, sender_user_id, body,
               client_msg_id, email_message_id, email_in_reply_to, created_at
          from messages
         where workspace_id = $1 and conversation_id = $2
         order by seq desc
         limit $3
        """,
        workspace_id,
        conversation_id,
        limit,
    )
    return list(reversed(rows))


async def get_last_inbound_email(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, email_message_id from messages
         where workspace_id = $1 and conversation_id = $2
           and sender_type = 'contact' and email_message_id is not null
         order by seq desc
         limit 1
        """,
        workspace_id,
        conversation_id,
    )
