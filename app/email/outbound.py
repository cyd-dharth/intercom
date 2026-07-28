import uuid
from email.message import EmailMessage

import asyncpg

from app.config import settings
from app.repositories import messages as messages_repo
from app.security import hmac10

MAX_REFERENCES = 9  # first plus last 8, per section 9.1


def reply_to_address(conversation_id: uuid.UUID) -> str:
    local, _, domain = settings.support_email.partition("@")
    conv_short = str(conversation_id).replace("-", "")[:8]
    tag = hmac10(str(conversation_id))
    return f"{local}+c{conv_short}.{tag}@{domain}"


def build_message_id(message_id: uuid.UUID) -> str:
    return f"<{message_id}@{settings.email_domain_for_message_id}>"


async def _references_chain(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> list[str]:
    rows = await messages_repo.list_recent(conn, workspace_id, conversation_id, limit=1000)
    ids = [r["email_message_id"] for r in rows if r["email_message_id"]]
    if len(ids) <= MAX_REFERENCES:
        return ids
    return [ids[0]] + ids[-(MAX_REFERENCES - 1):]


async def build_outbound_email(
    conn: asyncpg.Connection, conversation: asyncpg.Record, message: asyncpg.Record, to_addr: str
) -> tuple[bytes, str]:
    """Builds the MIME reply for an agent message. Threading headers let a normal mail
    client (and our own inbound resolver) place the reply in the same conversation even
    if the recipient's client mangles or strips the Reply-To fallback token."""
    workspace_id = conversation["workspace_id"]
    last_inbound = await messages_repo.get_last_inbound_email(conn, workspace_id, conversation["id"])
    references = await _references_chain(conn, workspace_id, conversation["id"])

    subject = conversation["subject"] or "Your conversation"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = EmailMessage()
    msg["Message-ID"] = build_message_id(message["id"])
    msg["From"] = settings.support_email
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Reply-To"] = reply_to_address(conversation["id"])
    if last_inbound is not None:
        msg["In-Reply-To"] = last_inbound["email_message_id"]
    if references:
        msg["References"] = " ".join(references)

    msg.set_content(message["body"])
    msg.add_alternative(f"<p>{_escape_html(message['body'])}</p>", subtype="html")

    return bytes(msg), subject


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


def build_invite_email(workspace_name: str, to_addr: str, invite_url: str) -> tuple[bytes, str]:
    """Builds the plain text plus HTML invite email. Not tied to a conversation, unlike
    build_outbound_email, so it carries no threading headers."""
    subject = f"You're invited to join {workspace_name} on Intercom"
    body = (
        f"You've been invited to join the \"{workspace_name}\" workspace on Intercom.\n\n"
        f"Accept the invite: {invite_url}\n\n"
        "This link expires in 7 days."
    )

    msg = EmailMessage()
    msg["From"] = settings.support_email
    msg["To"] = to_addr
    msg["Subject"] = subject

    msg.set_content(body)
    msg.add_alternative(
        f"<p>You've been invited to join the <strong>{_escape_html(workspace_name)}</strong> "
        f"workspace on Intercom.</p><p><a href=\"{invite_url}\">Accept the invite</a></p>"
        "<p>This link expires in 7 days.</p>",
        subtype="html",
    )

    return bytes(msg), subject
