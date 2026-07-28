import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.ai.summarizer import should_trigger_on_open
from app.db import get_pool
from app.deps import current_membership
from app.errors import NotFoundError, ValidationAppError
from app.ratelimit import check_rate_limit
from app.repositories import ai as ai_repo
from app.repositories import conversations as conversations_repo
from app.repositories import jobs as jobs_repo
from app.repositories import messages as messages_repo
from app.services.conversations import assign_conversation, send_message, set_conversation_status

router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations", tags=["conversations"])


def _conversation_out(c) -> dict:
    keys = c.keys()
    return {
        "id": str(c["id"]),
        "channel": c["channel"],
        "status": c["status"],
        "subject": c["subject"],
        "assignee_id": str(c["assignee_id"]) if c["assignee_id"] else None,
        "last_seq": c["last_seq"],
        "message_count": c["message_count"],
        "last_message_at": c["last_message_at"].isoformat() if c["last_message_at"] else None,
        "snoozed_until": c["snoozed_until"].isoformat() if c["snoozed_until"] else None,
        "contact_name": c["contact_name"] if "contact_name" in keys else None,
        "contact_email": c["contact_email"] if "contact_email" in keys else None,
        "contact_visitor_id": c["contact_visitor_id"] if "contact_visitor_id" in keys else None,
    }


def _message_out(m) -> dict:
    return {
        "id": str(m["id"]),
        "seq": m["seq"],
        "sender_type": m["sender_type"],
        "sender_user_id": str(m["sender_user_id"]) if m["sender_user_id"] else None,
        "body": m["body"],
        "created_at": m["created_at"].isoformat(),
    }


class SendMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    client_msg_id: str | None = None


class AssignRequest(BaseModel):
    assignee_id: uuid.UUID | None = None


class SnoozeRequest(BaseModel):
    snoozed_until: datetime


@router.get("")
async def list_conversations(
    workspace_id: uuid.UUID,
    request: Request,
    status: str | None = None,
    channel: str | None = None,
    assignee_id: uuid.UUID | None = None,
):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conversations_repo.list_inbox(conn, workspace_id, status=status, channel=channel, assignee_id=assignee_id)
    return {"conversations": [_conversation_out(r) for r in rows]}


def _summary_out(row) -> dict | None:
    if row is None:
        return None
    return {
        "conversation_id": str(row["conversation_id"]),
        "summary": dict(row["summary"]),
        "covered_through_seq": row["covered_through_seq"],
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "generator": row["generator"],
        "updated_at": row["updated_at"].isoformat(),
    }


@router.get("/{conversation_id}")
async def get_conversation(workspace_id: uuid.UUID, conversation_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        messages = await messages_repo.list_recent(conn, workspace_id, conversation_id, limit=200)
        summary_row = await ai_repo.get_summary(conn, workspace_id, conversation_id)
        covered_through_seq = summary_row["covered_through_seq"] if summary_row else 0

        # Agent-opens-conversation trigger per CLAUDE.md section 10.2: serve whatever is
        # stored immediately (with a staleness flag from covered_through_seq vs last_seq)
        # and enqueue a background refresh rather than blocking the page render.
        if should_trigger_on_open(conversation, covered_through_seq):
            await jobs_repo.enqueue(
                conn,
                kind="summarize",
                payload={"workspace_id": str(workspace_id), "conversation_id": str(conversation_id)},
                workspace_id=workspace_id,
                dedupe_key=f"summary:{conversation_id}",
            )

    return {
        "conversation": {
            "id": str(conversation["id"]),
            "channel": conversation["channel"],
            "status": conversation["status"],
            "subject": conversation["subject"],
            "assignee_id": str(conversation["assignee_id"]) if conversation["assignee_id"] else None,
            "last_seq": conversation["last_seq"],
            "message_count": conversation["message_count"],
        },
        "messages": [_message_out(m) for m in messages],
        "summary": _summary_out(summary_row),
    }


@router.post("/{conversation_id}/summary/regenerate")
async def regenerate_summary(workspace_id: uuid.UUID, conversation_id: uuid.UUID, request: Request):
    """Manual regenerate button per section 10.5 step 3. Enqueues immediately rather
    than calling the LLM inline, keeping AI work out of the request path per rule 7."""
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        job = await jobs_repo.enqueue(
            conn,
            kind="summarize",
            payload={"workspace_id": str(workspace_id), "conversation_id": str(conversation_id)},
            workspace_id=workspace_id,
            dedupe_key=f"summary:{conversation_id}",
        )
    return {"status": "enqueued" if job else "already_pending"}


@router.post("/{conversation_id}/messages")
async def post_message(workspace_id: uuid.UUID, conversation_id: uuid.UUID, body: SendMessageRequest, request: Request):
    membership = await current_membership(request, workspace_id)
    check_rate_limit("message_send", str(membership.user.id))
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        message, _ = await send_message(
            conn,
            workspace_id,
            conversation_id,
            sender_type="agent",
            body=body.body,
            sender_user_id=membership.user.id,
            client_msg_id=body.client_msg_id,
        )
    return {"message": _message_out(message)}


@router.post("/{conversation_id}/assign")
async def assign(workspace_id: uuid.UUID, conversation_id: uuid.UUID, body: AssignRequest, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        conversation = await assign_conversation(conn, workspace_id, conversation_id, body.assignee_id)
    return {"conversation": _conversation_out(conversation)}


@router.post("/{conversation_id}/snooze")
async def snooze(workspace_id: uuid.UUID, conversation_id: uuid.UUID, body: SnoozeRequest, request: Request):
    await current_membership(request, workspace_id)
    snoozed_until = body.snoozed_until
    if snoozed_until.tzinfo is None:
        snoozed_until = snoozed_until.replace(tzinfo=timezone.utc)
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        conversation = await set_conversation_status(conn, workspace_id, conversation_id, "snoozed", snoozed_until)
    return {"conversation": _conversation_out(conversation)}


@router.post("/{conversation_id}/resolve")
async def resolve(workspace_id: uuid.UUID, conversation_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        conversation = await set_conversation_status(conn, workspace_id, conversation_id, "resolved")
    return {"conversation": _conversation_out(conversation)}


@router.post("/{conversation_id}/reopen")
async def reopen(workspace_id: uuid.UUID, conversation_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        conversation = await set_conversation_status(conn, workspace_id, conversation_id, "open")
    return {"conversation": _conversation_out(conversation)}
