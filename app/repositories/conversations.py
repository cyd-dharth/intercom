import uuid

import asyncpg


async def create_conversation(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    contact_id: uuid.UUID,
    channel: str,
    subject: str | None = None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into conversations (workspace_id, contact_id, channel, subject)
        values ($1, $2, $3, $4)
        returning id, workspace_id, contact_id, channel, status, subject, assignee_id,
                  last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
        """,
        workspace_id,
        contact_id,
        channel,
        subject,
    )


async def get_by_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, contact_id, channel, status, subject, assignee_id,
               last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
          from conversations
         where workspace_id = $1 and id = $2
        """,
        workspace_id,
        conversation_id,
    )


async def bump_seq(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> int:
    """Row locking transaction bump. Must be called inside a transaction. Returns new last_seq."""
    row = await conn.fetchrow(
        """
        update conversations
           set last_seq = last_seq + 1,
               message_count = message_count + 1,
               last_message_at = now()
         where id = $1 and workspace_id = $2
        returning last_seq
        """,
        conversation_id,
        workspace_id,
    )
    return row["last_seq"]


async def set_first_response_at(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    await conn.execute(
        """
        update conversations
           set first_response_at = now()
         where id = $1 and workspace_id = $2 and first_response_at is null
        """,
        conversation_id,
        workspace_id,
    )


async def list_inbox(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    status: str | None = None,
    channel: str | None = None,
    assignee_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[asyncpg.Record]:
    conditions = ["c.workspace_id = $1"]
    params: list = [workspace_id]
    if status:
        params.append(status)
        conditions.append(f"c.status = ${len(params)}")
    if channel:
        params.append(channel)
        conditions.append(f"c.channel = ${len(params)}")
    if assignee_id:
        params.append(assignee_id)
        conditions.append(f"c.assignee_id = ${len(params)}")
    params.append(limit)
    where_clause = " and ".join(conditions)
    query = f"""
        select c.id, c.workspace_id, c.contact_id, c.channel, c.status, c.subject, c.assignee_id,
               c.last_seq, c.message_count, c.last_message_at, c.snoozed_until, c.first_response_at, c.created_at,
               ct.name as contact_name, ct.email as contact_email, ct.visitor_id as contact_visitor_id
          from conversations c
          join contacts ct on ct.id = c.contact_id
         where {where_clause}
         order by c.last_message_at desc nulls last
         limit ${len(params)}
    """
    return await conn.fetch(query, *params)


async def assign(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID, assignee_id: uuid.UUID | None) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update conversations
           set assignee_id = $3
         where id = $1 and workspace_id = $2
        returning id, workspace_id, contact_id, channel, status, subject, assignee_id,
                  last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
        """,
        conversation_id,
        workspace_id,
        assignee_id,
    )


async def set_status(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    status: str,
    snoozed_until=None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update conversations
           set status = $3, snoozed_until = $4
         where id = $1 and workspace_id = $2
        returning id, workspace_id, contact_id, channel, status, subject, assignee_id,
                  last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
        """,
        conversation_id,
        workspace_id,
        status,
        snoozed_until,
    )


async def find_by_short_id_prefix_any_workspace(conn: asyncpg.Connection, short_id: str) -> list[asyncpg.Record]:
    """Unscoped by workspace_id, unlike every other query in this module. Used only to
    resolve the Reply-To fallback token on inbound email, where the workspace is not yet
    known and the HMAC signature (keyed by conversation id) is the actual security
    boundary, not the WHERE clause. Never use this for anything else."""
    return await conn.fetch(
        """
        select id, workspace_id, contact_id, channel, status, subject, assignee_id,
               last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
          from conversations
         where replace(id::text, '-', '') like $1 || '%'
        """,
        short_id,
    )


async def find_open_by_subject_and_contact(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, contact_id: uuid.UUID, normalized_subject: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, contact_id, channel, status, subject, assignee_id,
               last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
          from conversations
         where workspace_id = $1 and contact_id = $2 and channel = 'email'
           and status != 'resolved'
           and created_at > now() - interval '30 days'
           and lower(regexp_replace(coalesce(subject, ''), '^(re|fwd?)\\s*:\\s*', '', 'gi')) = $3
         order by last_message_at desc nulls last
         limit 1
        """,
        workspace_id,
        contact_id,
        normalized_subject,
    )


async def list_open_conversations_for_contact(conn: asyncpg.Connection, workspace_id: uuid.UUID, contact_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, workspace_id, contact_id, channel, status, subject, assignee_id,
               last_seq, message_count, last_message_at, snoozed_until, first_response_at, created_at
          from conversations
         where workspace_id = $1 and contact_id = $2
         order by last_message_at desc nulls last
        """,
        workspace_id,
        contact_id,
    )
