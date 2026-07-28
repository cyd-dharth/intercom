import logging
import uuid

import asyncpg

from app.ai.client import embed_text
from app.ai.summarizer import refresh_summary
from app.email.client import send_smtp
from app.email.outbound import build_invite_email, build_outbound_email
from app.logging import log_extra
from app.repositories import conversations as conversations_repo
from app.repositories import domains as domains_repo
from app.repositories import jobs as jobs_repo
from app.repositories import kb as kb_repo
from app.repositories import messages as messages_repo
from app.repositories import workspaces as workspaces_repo
from app.services.conversations import set_conversation_status
from app.services.domains import MAX_VERIFY_ATTEMPTS, VERIFY_RETRY_SECONDS, run_verification

logger = logging.getLogger("app.jobs.handlers")


async def handle_send_email(conn: asyncpg.Connection, payload: dict) -> None:
    message_id = payload["message_id"]
    message = await messages_repo.get_by_id(conn, payload["workspace_id"], message_id)
    if message is None:
        logger.warning("send_email job found no message, skipping", extra=log_extra(message_id=message_id))
        return
    conversation = await conversations_repo.get_by_id(conn, message["workspace_id"], message["conversation_id"])
    if conversation is None:
        logger.warning("send_email job found no conversation, skipping", extra=log_extra(message_id=message_id))
        return
    if conversation["channel"] != "email":
        return
    to_addr = payload["to_email"]
    mime_bytes, subject = await build_outbound_email(conn, conversation, message, to_addr)
    send_smtp(to_addr, mime_bytes)
    logger.info(
        "outbound email sent",
        extra=log_extra(conversation_id=str(conversation["id"]), workspace_id=str(conversation["workspace_id"])),
    )


async def handle_send_invite_email(conn: asyncpg.Connection, payload: dict) -> None:
    """Sends the invite link by email so the invitee does not have to be handed the
    token out of band. Best effort: if SMTP is not configured, the invite still exists
    and its token is still returned from the invite API, so the admin can share the
    link manually as a fallback."""
    to_addr = payload["to_email"]
    workspace_id = uuid.UUID(payload["workspace_id"])
    workspace = await workspaces_repo.get_workspace_by_id(conn, workspace_id)
    if workspace is None:
        return
    mime_bytes, _ = build_invite_email(workspace["name"], to_addr, payload["invite_url"])
    send_smtp(to_addr, mime_bytes)
    logger.info("invite email sent", extra=log_extra(workspace_id=str(workspace_id)))


async def handle_unsnooze(conn: asyncpg.Connection, payload: dict) -> None:
    """run_after on the job already ensures this fires no earlier than snoozed_until,
    so only the current status needs checking (an agent may have resolved or reopened
    the conversation manually before this job ran)."""
    conversation_id = payload["conversation_id"]
    workspace_id = payload["workspace_id"]
    conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
    if conversation is None or conversation["status"] != "snoozed":
        return
    await set_conversation_status(conn, workspace_id, conversation_id, "open")


async def handle_embed_article(conn: asyncpg.Connection, payload: dict) -> None:
    workspace_id = uuid.UUID(payload["workspace_id"])
    article_id = uuid.UUID(payload["article_id"])
    chunks = await kb_repo.list_chunks_without_embedding(conn, workspace_id, article_id)
    for chunk in chunks:
        embedding = await embed_text(chunk["content"], workspace_id=workspace_id)
        if embedding is not None:
            await kb_repo.set_chunk_embedding(conn, chunk["id"], embedding)
        else:
            logger.warning(
                "embedding failed for chunk, left unembedded, falls back to lexical only search",
                extra=log_extra(article_id=str(article_id), chunk_id=str(chunk["id"])),
            )


async def handle_summarize(conn: asyncpg.Connection, payload: dict) -> None:
    workspace_id = uuid.UUID(payload["workspace_id"])
    conversation_id = uuid.UUID(payload["conversation_id"])
    await refresh_summary(conn, workspace_id, conversation_id)


async def handle_verify_domain(conn: asyncpg.Connection, payload: dict) -> None:
    """Runs one DNS check per section 12 step 2. The job's own attempts/max_attempts
    columns drive the generic exception based retry in app/jobs/worker.py, which is a
    different concept from this: verification not yet succeeding is not an exception,
    it is the expected common case while a customer is still updating DNS. So attempt
    counting for the 60 second, 10 attempt reschedule lives in the payload itself, and
    this handler self-enqueues the next attempt rather than raising. The dedupe key
    includes the attempt number so the self-enqueue never collides with this job's own
    row, which is still status='running' (and so still holds its own dedupe key) for
    the remainder of this call."""
    workspace_id = uuid.UUID(payload["workspace_id"])
    domain_id = uuid.UUID(payload["domain_id"])
    attempt = payload.get("attempt", 1)

    domain = await domains_repo.get_by_id(conn, workspace_id, domain_id)
    if domain is None:
        return
    if domain["status"] == "verified":
        return

    domain = await run_verification(conn, domain)
    if domain is None or domain["status"] == "verified":
        return

    if attempt >= MAX_VERIFY_ATTEMPTS:
        await domains_repo.mark_failed(conn, domain_id, domain["last_error"] or "verification did not succeed after maximum attempts")
        logger.info("custom domain verification exhausted attempts", extra=log_extra(hostname=domain["hostname"], attempts=attempt))
        return

    await jobs_repo.enqueue(
        conn,
        kind="verify_domain",
        payload={"workspace_id": str(workspace_id), "domain_id": str(domain_id), "attempt": attempt + 1},
        workspace_id=workspace_id,
        dedupe_key=f"verify_domain:{domain_id}:{attempt + 1}",
        run_after_seconds=VERIFY_RETRY_SECONDS,
    )


HANDLERS = {
    "send_email": handle_send_email,
    "send_invite_email": handle_send_invite_email,
    "unsnooze": handle_unsnooze,
    "embed_article": handle_embed_article,
    "summarize": handle_summarize,
    "verify_domain": handle_verify_domain,
}
