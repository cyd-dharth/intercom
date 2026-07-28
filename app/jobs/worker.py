import asyncio
import logging

from app.db import get_pool
from app.jobs.handlers import HANDLERS
from app.logging import log_extra
from app.repositories import jobs as jobs_repo

logger = logging.getLogger("app.jobs.worker")

POLL_INTERVAL_SECONDS = 0.5


async def _run_one_claim() -> bool:
    """Claims and dispatches a single job. Returns True if a job was claimed."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            job = await jobs_repo.claim_one(conn)
        if job is None:
            return False

        handler = HANDLERS.get(job["kind"])
        if handler is None:
            logger.error("no handler for job kind", extra=log_extra(kind=job["kind"], job_id=job["id"]))
            await jobs_repo.mark_failed(conn, job["id"], job["attempts"], job["max_attempts"], f"no handler for kind {job['kind']}")
            return True

        try:
            async with conn.transaction():
                await handler(conn, job["payload"])
            await jobs_repo.mark_done(conn, job["id"])
        except Exception as exc:
            logger.exception("job failed", extra=log_extra(kind=job["kind"], job_id=job["id"], attempts=job["attempts"]))
            await jobs_repo.mark_failed(conn, job["id"], job["attempts"], job["max_attempts"], str(exc))
    return True


async def worker_loop(worker_id: int) -> None:
    while True:
        try:
            claimed = await _run_one_claim()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker loop error", extra=log_extra(worker_id=worker_id))
            claimed = False
        if not claimed:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
