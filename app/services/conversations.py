import logging
import uuid
from datetime import datetime

import asyncpg

from app.ai.summarizer import should_trigger_on_new_message
from app.config import settings
from app.errors import ValidationAppError
from app.realtime.bus import bus
from app.repositories import ai as ai_repo
from app.repositories import contacts as contacts_repo
from app.repositories import conversations as conversations_repo
from app.repositories import jobs as jobs_repo
from app.repositories import messages as messages_repo

logger = logging.getLogger("app.services.conversations")

MAX_BODY_LENGTH = 10000


def _message_out(message: asyncpg.Record) -> dict:
    return {
        "id": str(message["id"]),
        "conversation_id": str(message["conversation_id"]),
        "seq": message["seq"],
        "sender_type": message["sender_type"],
        "sender_user_id": str(message["sender_user_id"]) if message["sender_user_id"] else None,
        "body": message["body"],
        "client_msg_id": message["client_msg_id"],
        "created_at": message["created_at"].isoformat(),
    }


def _conversation_out(conversation: asyncpg.Record) -> dict:
    return {
        "id": str(conversation["id"]),
        "workspace_id": str(conversation["workspace_id"]),
        "contact_id": str(conversation["contact_id"]),
        "channel": conversation["channel"],
        "status": conversation["status"],
        "subject": conversation["subject"],
        "assignee_id": str(conversation["assignee_id"]) if conversation["assignee_id"] else None,
        "last_seq": conversation["last_seq"],
        "message_count": conversation["message_count"],
        "last_message_at": conversation["last_message_at"].isoformat() if conversation["last_message_at"] else None,
        "snoozed_until": conversation["snoozed_until"].isoformat() if conversation["snoozed_until"] else None,
        "created_at": conversation["created_at"].isoformat(),
    }


async def send_message(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    sender_type: str,
    body: str,
    sender_user_id: uuid.UUID | None = None,
    client_msg_id: str | None = None,
    email_message_id: str | None = None,
    email_in_reply_to: str | None = None,
) -> tuple[asyncpg.Record, bool]:
    """Inserts a message under the ordering invariant: the seq bump and the message
    insert happen in one transaction, serialised by the conversations row lock.
    Idempotent on (conversation_id, client_msg_id): a retry returns the existing row.
    Returns (message, was_created)."""
    if len(body) > MAX_BODY_LENGTH:
        raise ValidationAppError(f"Message body exceeds {MAX_BODY_LENGTH} characters", code="body_too_long")
    if not body.strip():
        raise ValidationAppError("Message body cannot be empty", code="body_empty")

    if client_msg_id:
        existing = await messages_repo.get_by_client_msg_id(conn, workspace_id, conversation_id, client_msg_id)
        if existing is not None:
            return existing, False

    conversation_before = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
    is_agent_email_reply = (
        sender_type == "agent" and conversation_before is not None and conversation_before["channel"] == "email"
    )
    if is_agent_email_reply and email_message_id is None:
        from app.email.outbound import build_message_id

        outbound_message_id = uuid.uuid4()
        email_message_id = build_message_id(outbound_message_id)
    else:
        outbound_message_id = None

    async with conn.transaction():
        seq = await conversations_repo.bump_seq(conn, workspace_id, conversation_id)
        message = await messages_repo.insert_message(
            conn,
            workspace_id,
            conversation_id,
            seq,
            sender_type,
            body,
            sender_user_id=sender_user_id,
            client_msg_id=client_msg_id,
            email_message_id=email_message_id,
            email_in_reply_to=email_in_reply_to,
            message_id=outbound_message_id,
        )
        if sender_type == "agent":
            await conversations_repo.set_first_response_at(conn, workspace_id, conversation_id)
        if is_agent_email_reply:
            to_addr = await contacts_repo.get_reply_address(conn, workspace_id, conversation_id)
            if to_addr and settings.email_enabled():
                await jobs_repo.enqueue(
                    conn,
                    kind="send_email",
                    payload={"message_id": str(message["id"]), "workspace_id": str(workspace_id), "to_email": to_addr},
                    workspace_id=workspace_id,
                )

    conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)

    await bus.publish(f"conv:{conversation_id}", {"type": "message.new", "data": {"message": _message_out(message)}})
    await bus.publish(
        f"ws:{workspace_id}",
        {"type": "conversation.updated", "data": {"conversation": _conversation_out(conversation)}},
    )

    await _maybe_enqueue_summary_on_new_message(conn, workspace_id, conversation_id, conversation)

    return message, True


async def _maybe_enqueue_summary_on_new_message(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID, conversation: asyncpg.Record
) -> None:
    """New message trigger rule per CLAUDE.md section 10.2: only enqueue once the gap
    since the last covered summary reaches the debounce threshold. The dedupe_key's
    partial unique index collapses repeated enqueues into one pending job, which is the
    debounce itself, run_after 20 seconds later lets a burst of messages land first."""
    existing_summary = await ai_repo.get_summary(conn, workspace_id, conversation_id)
    covered_through_seq = existing_summary["covered_through_seq"] if existing_summary else 0
    if should_trigger_on_new_message(conversation, covered_through_seq):
        await jobs_repo.enqueue(
            conn,
            kind="summarize",
            payload={"workspace_id": str(workspace_id), "conversation_id": str(conversation_id)},
            workspace_id=workspace_id,
            dedupe_key=f"summary:{conversation_id}",
            run_after_seconds=20,
        )


async def get_or_create_visitor_conversation(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, contact_id: uuid.UUID
) -> asyncpg.Record:
    open_convs = await conversations_repo.list_open_conversations_for_contact(conn, workspace_id, contact_id)
    for conv in open_convs:
        if conv["channel"] == "chat" and conv["status"] != "resolved":
            return conv
    return await conversations_repo.create_conversation(conn, workspace_id, contact_id, "chat")


async def assign_conversation(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID, assignee_id: uuid.UUID | None
) -> asyncpg.Record:
    conversation = await conversations_repo.assign(conn, workspace_id, conversation_id, assignee_id)
    await bus.publish(
        f"ws:{workspace_id}",
        {"type": "conversation.updated", "data": {"conversation": _conversation_out(conversation)}},
    )
    return conversation


async def set_conversation_status(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    status: str,
    snoozed_until: datetime | None = None,
) -> asyncpg.Record:
    if status not in ("open", "snoozed", "resolved"):
        raise ValidationAppError("Invalid status", code="invalid_status")
    if status == "snoozed" and snoozed_until is None:
        raise ValidationAppError("snoozed_until is required when snoozing", code="snoozed_until_required")
    conversation = await conversations_repo.set_status(conn, workspace_id, conversation_id, status, snoozed_until)
    if status == "snoozed":
        delay_seconds = max(0.0, (snoozed_until - datetime.now(snoozed_until.tzinfo)).total_seconds())
        await jobs_repo.enqueue(
            conn,
            kind="unsnooze",
            payload={"conversation_id": str(conversation_id), "workspace_id": str(workspace_id)},
            workspace_id=workspace_id,
            dedupe_key=f"unsnooze:{conversation_id}",
            run_after_seconds=delay_seconds,
        )
    await bus.publish(
        f"ws:{workspace_id}",
        {"type": "conversation.updated", "data": {"conversation": _conversation_out(conversation)}},
    )
    return conversation
