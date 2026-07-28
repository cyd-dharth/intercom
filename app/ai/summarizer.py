import json
import logging
import uuid

import asyncpg

from app.ai.client import generate_json
from app.ai.prompts import SUMMARY_PROMPT_TEMPLATE, SUMMARY_PROMPT_VERSION
from app.ai.schemas import ConversationSummary
from app.config import settings
from app.realtime.bus import bus
from app.repositories import ai as ai_repo
from app.repositories import conversations as conversations_repo
from app.repositories import messages as messages_repo

logger = logging.getLogger("app.ai.summarizer")

MAX_NEW_MESSAGES = 30
MAX_MESSAGE_CHARS = 2000
MIN_MESSAGE_COUNT_FOR_LLM = 6
NEW_MESSAGE_DEBOUNCE_THRESHOLD = 5


def _truncate_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-(max_chars // 2) :]
    return f"{head}\n...[truncated]...\n{tail}"


def _format_messages(messages: list[asyncpg.Record]) -> str:
    lines = []
    for m in messages:
        speaker = {"contact": "Customer", "agent": "Agent", "system": "System"}.get(m["sender_type"], m["sender_type"])
        body = _truncate_middle(m["body"], MAX_MESSAGE_CHARS)
        lines.append(f"[seq {m['seq']}] {speaker}: {body}")
    return "\n".join(lines)


def _extractive_summary(messages: list[asyncpg.Record]) -> dict:
    """No-LLM fallback per section 10.5 step 2, used when no summary exists yet and the
    LLM is unavailable (disabled, budget exceeded, or every call failed)."""
    first_contact_msg = next((m["body"] for m in messages if m["sender_type"] == "contact"), "")
    tail = messages[-3:]
    current_status = " | ".join(_truncate_middle(m["body"], 200) for m in tail) if tail else ""
    return {
        "what_user_wants": _truncate_middle(first_contact_msg, 200) if first_contact_msg else "Not enough information yet.",
        "what_has_been_tried": [],
        "current_status": current_status or "No messages yet.",
        "open_questions": [],
        "suggested_next_action": "Review the conversation manually.",
        "sentiment": "neutral",
        "confidence": 0.3,
    }


async def refresh_summary(conn: asyncpg.Connection, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> dict:
    """Implements the incremental summarisation algorithm in CLAUDE.md section 10.2:
    load the watermark, fetch only messages after it (capped, truncated), prompt with
    only the delta, and upsert with the new watermark. Cost scales with new messages,
    not conversation length. On any failure the previous stored summary survives."""
    existing = await ai_repo.get_summary(conn, workspace_id, conversation_id)
    previous_summary = dict(existing["summary"]) if existing else None
    covered_through_seq = existing["covered_through_seq"] if existing else 0

    conversation = await conversations_repo.get_by_id(conn, workspace_id, conversation_id)
    if conversation is None:
        raise ValueError("conversation not found")

    new_messages = await messages_repo.list_since_seq(conn, workspace_id, conversation_id, covered_through_seq)
    new_messages = new_messages[:MAX_NEW_MESSAGES]

    if not new_messages and existing is not None:
        return _summary_out(existing)

    if not new_messages and existing is None:
        all_messages = await messages_repo.list_recent(conn, workspace_id, conversation_id, limit=MAX_NEW_MESSAGES)
        extractive = _extractive_summary(all_messages)
        row = await ai_repo.upsert_summary(
            conn, workspace_id, conversation_id, extractive, conversation["last_seq"], "none", SUMMARY_PROMPT_VERSION, "extractive"
        )
        await _publish(row)
        return _summary_out(row)

    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        previous_summary_json=json.dumps(previous_summary or {}),
        new_messages=_format_messages(new_messages),
    )
    new_covered_through_seq = new_messages[-1]["seq"]

    parsed, status = await generate_json(
        prompt=prompt,
        schema=ConversationSummary,
        workspace_id=workspace_id,
        kind="summarize",
        prompt_version=SUMMARY_PROMPT_VERSION,
    )

    if parsed is not None:
        row = await ai_repo.upsert_summary(
            conn,
            workspace_id,
            conversation_id,
            parsed.model_dump(),
            new_covered_through_seq,
            settings.llm_model_primary,
            SUMMARY_PROMPT_VERSION,
            "llm",
        )
        await _publish(row)
        return _summary_out(row)

    logger.warning(
        "summary generation failed, degrading",
        extra={"extra_fields": {"conversation_id": str(conversation_id), "status": status}},
    )

    if existing is not None:
        return _summary_out(existing)

    all_messages = await messages_repo.list_recent(conn, workspace_id, conversation_id, limit=MAX_NEW_MESSAGES)
    extractive = _extractive_summary(all_messages)
    row = await ai_repo.upsert_summary(
        conn, workspace_id, conversation_id, extractive, conversation["last_seq"], "none", SUMMARY_PROMPT_VERSION, "extractive"
    )
    await _publish(row)
    return _summary_out(row)


async def _publish(row: asyncpg.Record) -> None:
    await bus.publish(
        f"ws:{row['workspace_id']}",
        {
            "type": "summary.updated",
            "data": {
                "conversation_id": str(row["conversation_id"]),
                "summary": _summary_out(row),
            },
        },
    )


def _summary_out(row: asyncpg.Record) -> dict:
    return {
        "conversation_id": str(row["conversation_id"]),
        "summary": dict(row["summary"]),
        "covered_through_seq": row["covered_through_seq"],
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "generator": row["generator"],
        "updated_at": row["updated_at"].isoformat(),
    }


def should_trigger_on_open(conversation: asyncpg.Record, covered_through_seq: int) -> bool:
    return conversation["last_seq"] > covered_through_seq and conversation["message_count"] >= MIN_MESSAGE_COUNT_FOR_LLM


def should_trigger_on_new_message(conversation: asyncpg.Record, covered_through_seq: int) -> bool:
    return conversation["last_seq"] - covered_through_seq >= NEW_MESSAGE_DEBOUNCE_THRESHOLD
