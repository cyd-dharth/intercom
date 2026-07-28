import logging
import re
import uuid
from email.message import Message
from email.utils import getaddresses, parseaddr

import asyncpg

from app.config import settings
from app.repositories import contacts as contacts_repo
from app.repositories import conversations as conversations_repo
from app.repositories import messages as messages_repo
from app.repositories import workspaces as workspaces_repo
from app.security import verify_hmac10
from app.services.conversations import send_message

logger = logging.getLogger("app.email.inbound")

_QUOTE_MARKERS = [
    re.compile(r"^On .* wrote:\s*$"),
    re.compile(r"^From:\s", re.IGNORECASE),
    re.compile(r"^Sent from my"),
    re.compile(r"^>"),
    re.compile(r"^-----Original Message-----"),
]

_RE_FWD_PREFIX = re.compile(r"^\s*(re|fwd?)\s*:\s*", re.IGNORECASE)
_REPLY_TAG = re.compile(r"\+c([0-9a-f]{8})\.([0-9a-f]{10})", re.IGNORECASE)
_WS_TAG = re.compile(r"\+ws([a-z0-9-]+)", re.IGNORECASE)


def extract_text_body(msg: Message) -> str:
    if msg.is_multipart():
        plain = None
        html = None
        for part in msg.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain" and plain is None:
                plain = part.get_content()
            elif content_type == "text/html" and html is None:
                html = part.get_content()
        if plain is not None:
            return plain
        if html is not None:
            return _strip_html(html)
        return ""
    content_type = msg.get_content_type()
    content = msg.get_content()
    if content_type == "text/html":
        return _strip_html(content)
    return content


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def strip_quoted_reply(body: str) -> str:
    lines = body.splitlines()
    kept: list[str] = []
    for line in lines:
        if any(marker.match(line.strip()) for marker in _QUOTE_MARKERS):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def normalize_subject(subject: str | None) -> str:
    subject = subject or ""
    while True:
        new_subject = _RE_FWD_PREFIX.sub("", subject)
        if new_subject == subject:
            break
        subject = new_subject
    return " ".join(subject.split()).strip().lower()


def parse_reply_tag(addresses: list[str]) -> tuple[str, str] | None:
    """Extracts (conv_short, hmac_tag) from any address containing the +c token,
    e.g. support+c1a2b3c4d.0123456789ab@domain. Verifies the HMAC before returning."""
    for addr in addresses:
        match = _REPLY_TAG.search(addr)
        if match:
            return match.group(1), match.group(2)
    return None


def parse_workspace_tag(addresses: list[str]) -> str | None:
    for addr in addresses:
        match = _WS_TAG.search(addr)
        if match:
            return match.group(1)
    return None


async def resolve_conversation_by_references(
    conn: asyncpg.Connection, references: list[str]
) -> asyncpg.Record | None:
    """Step 1 of the resolution chain, workspace agnostic: email_message_id is globally
    unique, so the workspace itself falls out of whichever message matches."""
    for ref in references:
        message = await messages_repo.find_by_email_message_id_any_workspace(conn, ref)
        if message is not None:
            return await conversations_repo.get_by_id(conn, message["workspace_id"], message["conversation_id"])
    return None


async def resolve_conversation_by_reply_tag(
    conn: asyncpg.Connection, to_addresses: list[str]
) -> asyncpg.Record | None:
    """Step 2 of the resolution chain, also workspace agnostic since the Reply-To token
    is the only signal available before a workspace is known. The HMAC check, not the
    WHERE clause, is what makes this safe to run unscoped."""
    tag = parse_reply_tag(to_addresses)
    if tag is None:
        return None
    conv_short, hmac_tag = tag
    candidates = await conversations_repo.find_by_short_id_prefix_any_workspace(conn, conv_short)
    for candidate in candidates:
        if verify_hmac10(str(candidate["id"]), hmac_tag):
            return candidate
    return None


async def resolve_conversation_by_subject(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, from_email: str, subject: str | None
) -> asyncpg.Record | None:
    """Step 3, scoped to a workspace already resolved via the +ws tag or the fallback slug."""
    normalized = normalize_subject(subject)
    if not normalized:
        return None
    contact = await contacts_repo.get_by_email(conn, workspace_id, from_email)
    if contact is None:
        return None
    return await conversations_repo.find_open_by_subject_and_contact(conn, workspace_id, contact["id"], normalized)


def resolve_workspace_slug(to_addresses: list[str]) -> str:
    ws_tag = parse_workspace_tag(to_addresses)
    return ws_tag or settings.email_fallback_workspace_slug


async def process_inbound_email(conn: asyncpg.Connection, raw_msg: Message) -> None:
    message_id = raw_msg.get("Message-ID")
    from_name, from_email = parseaddr(raw_msg.get("From", ""))
    to_addresses = [addr for _, addr in getaddresses(raw_msg.get_all("To", []) + raw_msg.get_all("Delivered-To", []))]
    subject = raw_msg.get("Subject")

    if not from_email:
        logger.warning("inbound email: no From address, dropping")
        return

    references_header = raw_msg.get("References", "")
    in_reply_to = raw_msg.get("In-Reply-To")
    references = [r for r in references_header.split() if r]
    if in_reply_to:
        references = [in_reply_to] + references

    # Resolution chain, in order. Steps 1 and 2 are workspace agnostic: they identify
    # the conversation directly, and the workspace falls out of it. Step 3 needs a
    # workspace already resolved via the +ws tag or the fallback slug.
    conversation = await resolve_conversation_by_references(conn, references)
    strategy = "in_reply_to_references" if conversation else None

    if conversation is None:
        conversation = await resolve_conversation_by_reply_tag(conn, to_addresses)
        strategy = "reply_to_token" if conversation else None

    if conversation is not None:
        workspace_id = conversation["workspace_id"]
    else:
        ws_slug = resolve_workspace_slug(to_addresses)
        workspace = await workspaces_repo.get_workspace_by_slug(conn, ws_slug)
        if workspace is None:
            logger.warning("inbound email: no workspace resolved, dropping", extra={"extra_fields": {"to": to_addresses}})
            return
        workspace_id = workspace["id"]

    if message_id:
        existing = await messages_repo.get_by_email_message_id(conn, workspace_id, message_id)
        if existing is not None:
            logger.info("inbound email: duplicate message_id, skipping", extra={"extra_fields": {"workspace_id": str(workspace_id)}})
            return

    body = strip_quoted_reply(extract_text_body(raw_msg))
    if not body:
        body = "(empty message)"

    if conversation is None:
        conversation = await resolve_conversation_by_subject(conn, workspace_id, from_email, subject)
        strategy = "subject_match" if conversation else "new"

    contact = await contacts_repo.create_email_contact(conn, workspace_id, from_email, from_name or None)

    if conversation is None:
        conversation = await conversations_repo.create_conversation(
            conn, workspace_id, contact["id"], "email", subject=subject
        )

    logger.info(
        "inbound email threading resolved",
        extra={
            "extra_fields": {
                "workspace_id": str(workspace_id),
                "conversation_id": str(conversation["id"]),
                "strategy": strategy,
            }
        },
    )

    await send_message(
        conn,
        workspace_id,
        conversation["id"],
        sender_type="contact",
        body=body,
        email_message_id=message_id,
        email_in_reply_to=in_reply_to,
    )
