import asyncio
import email
import email.policy
import logging

from app.config import settings
from app.db import get_pool
from app.email.client import open_imap
from app.email.inbound import process_inbound_email
from app.logging import log_extra
from app.repositories import email_sync_state as sync_repo

logger = logging.getLogger("app.email.poller")


def _fetch_new_messages_sync(last_uid: int) -> list[tuple[int, email.message.Message]]:
    """Blocking IMAP fetch, run in a thread. Returns [(uid, parsed_message), ...]
    ascending by uid. Raises on IMAP connection failure."""
    imap = open_imap()
    try:
        typ, data = imap.uid("search", None, f"UID {last_uid + 1}:*")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = [int(u) for u in data[0].split() if int(u) > last_uid]

        results = []
        for uid in uids:
            typ, data = imap.uid("fetch", str(uid), "(RFC822)")
            if typ != "OK" or not data or data[0] is None:
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            results.append((uid, msg))
        return results
    finally:
        try:
            imap.logout()
        except Exception:
            pass


async def _poll_once() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        last_uid = await sync_repo.get_last_uid(conn)
        results = await asyncio.to_thread(_fetch_new_messages_sync, last_uid)

        for uid, msg in results:
            try:
                async with conn.transaction():
                    await process_inbound_email(conn, msg)
                await sync_repo.set_last_uid(conn, uid)
            except Exception:
                logger.exception("failed to process inbound email, advancing past it", extra=log_extra(uid=uid))
                await sync_repo.set_last_uid(conn, uid)


async def poller_loop() -> None:
    if not settings.email_enabled():
        logger.warning("email poller disabled, SMTP/IMAP credentials not configured")
        return
    while True:
        try:
            await _poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("email poller error")
        await asyncio.sleep(settings.imap_poll_seconds)
