import uuid

import asyncpg


async def enqueue(
    conn: asyncpg.Connection,
    kind: str,
    payload: dict,
    workspace_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    run_after_seconds: float = 0,
) -> asyncpg.Record | None:
    """Inserts a pending job. When dedupe_key collides with an existing pending or
    running job, the partial unique index rejects the insert and this returns None,
    which is how the summarize debounce and unsnooze scheduling collapse repeats."""
    try:
        return await conn.fetchrow(
            """
            insert into jobs (workspace_id, kind, payload, dedupe_key, run_after)
            values ($1, $2, $3, $4, now() + ($5 || ' seconds')::interval)
            returning id, workspace_id, kind, payload, dedupe_key, run_after, attempts, max_attempts, status
            """,
            workspace_id,
            kind,
            payload,
            dedupe_key,
            str(run_after_seconds),
        )
    except asyncpg.UniqueViolationError:
        return None


async def claim_one(conn: asyncpg.Connection) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update jobs
           set status = 'running', attempts = attempts + 1, locked_at = now()
         where id = (
           select id from jobs
            where status = 'pending' and run_after <= now()
            order by run_after
            for update skip locked
            limit 1
         )
        returning id, workspace_id, kind, payload, dedupe_key, attempts, max_attempts
        """
    )


async def mark_done(conn: asyncpg.Connection, job_id: int) -> None:
    await conn.execute("update jobs set status = 'done' where id = $1", job_id)


async def mark_failed(conn: asyncpg.Connection, job_id: int, attempts: int, max_attempts: int, error: str) -> None:
    error = error[:2000]
    if attempts < max_attempts:
        backoff_seconds = 2 ** attempts
        await conn.execute(
            """
            update jobs
               set status = 'pending', run_after = now() + ($3 || ' seconds')::interval, last_error = $2
             where id = $1
            """,
            job_id,
            error,
            str(backoff_seconds),
        )
    else:
        await conn.execute(
            "update jobs set status = 'dead', last_error = $2 where id = $1",
            job_id,
            error,
        )


async def reset_stuck_jobs(conn: asyncpg.Connection) -> int:
    result = await conn.execute(
        """
        update jobs
           set status = 'pending', locked_at = null
         where status = 'running' and locked_at < now() - interval '5 minutes'
        """
    )
    return int(result.split()[-1]) if result else 0


async def count_pending(conn: asyncpg.Connection) -> int:
    row = await conn.fetchrow("select count(*) as c from jobs where status = 'pending'")
    return row["c"]


async def counts_by_kind_status(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select kind, status, count(*) as c
          from jobs
         group by kind, status
         order by kind, status
        """
    )
