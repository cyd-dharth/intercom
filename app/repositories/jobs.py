import asyncpg


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
