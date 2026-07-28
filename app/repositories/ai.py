import uuid

import asyncpg


async def get_summary(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select conversation_id, workspace_id, summary, covered_through_seq, model, prompt_version, generator, updated_at
          from conversation_summaries
         where workspace_id = $1 and conversation_id = $2
        """,
        workspace_id,
        conversation_id,
    )


async def upsert_summary(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    summary: dict,
    covered_through_seq: int,
    model: str,
    prompt_version: str,
    generator: str,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into conversation_summaries
            (conversation_id, workspace_id, summary, covered_through_seq, model, prompt_version, generator, updated_at)
        values ($1, $2, $3, $4, $5, $6, $7, now())
        on conflict (conversation_id) do update
           set summary = excluded.summary,
               covered_through_seq = excluded.covered_through_seq,
               model = excluded.model,
               prompt_version = excluded.prompt_version,
               generator = excluded.generator,
               updated_at = now()
        returning conversation_id, workspace_id, summary, covered_through_seq, model, prompt_version, generator, updated_at
        """,
        conversation_id,
        workspace_id,
        summary,
        covered_through_seq,
        model,
        prompt_version,
        generator,
    )


async def record_ai_call(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID | None,
    kind: str,
    model: str,
    prompt_version: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_micros: int,
    latency_ms: int | None,
    status: str,
    error: str | None,
) -> None:
    await conn.execute(
        """
        insert into ai_calls
            (workspace_id, kind, model, prompt_version, input_tokens, output_tokens, cost_micros, latency_ms, status, error)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        workspace_id,
        kind,
        model,
        prompt_version,
        input_tokens,
        output_tokens,
        cost_micros,
        latency_ms,
        status,
        error[:2000] if error else None,
    )


async def sum_cost_today(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> int:
    row = await conn.fetchrow(
        """
        select coalesce(sum(cost_micros), 0) as total
          from ai_calls
         where workspace_id = $1 and created_at > date_trunc('day', now())
        """,
        workspace_id,
    )
    return int(row["total"])


async def list_calls_today(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, kind, model, prompt_version, input_tokens, output_tokens, cost_micros, latency_ms, status, created_at
          from ai_calls
         where workspace_id = $1 and created_at > date_trunc('day', now())
         order by created_at desc
        """,
        workspace_id,
    )
