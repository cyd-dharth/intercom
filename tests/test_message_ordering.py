"""Message ordering invariant per CLAUDE.md section 6.1: seq comes from the server
side row lock on conversations, never from client supplied timestamps, and concurrent
senders on the same conversation still produce a gap free, strictly increasing sequence.
"""

import asyncio
import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.config import settings
from app.db import _init_connection
from app.repositories import contacts as contacts_repo
from app.repositories import conversations as conversations_repo
from app.repositories import messages as messages_repo
from app.services.conversations import send_message


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=6, statement_cache_size=0, init=_init_connection)
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_concurrent_sends_produce_gap_free_increasing_seq(pool):
    suffix = uuid.uuid4().hex[:8]
    async with pool.acquire() as conn:
        from app.repositories import workspaces as workspaces_repo

        workspace = await workspaces_repo.create_workspace(conn, f"Ordering {suffix}", f"ordering-{suffix}")
        contact = await contacts_repo.create_visitor_contact(conn, workspace["id"], f"visitor-{suffix}")
        conversation = await conversations_repo.create_conversation(conn, workspace["id"], contact["id"], "chat")

    async def send_one(i: int):
        async with pool.acquire() as conn:
            message, created = await send_message(
                conn,
                workspace["id"],
                conversation["id"],
                sender_type="contact",
                body=f"message {i}",
                client_msg_id=str(uuid.uuid4()),
            )
            return message["seq"], created

    results = await asyncio.gather(*(send_one(i) for i in range(20)))
    seqs = sorted(seq for seq, _ in results)
    assert seqs == list(range(1, 21))
    assert all(created for _, created in results)

    async with pool.acquire() as conn:
        stored = await messages_repo.list_recent(conn, workspace["id"], conversation["id"], limit=100)
    assert [m["seq"] for m in stored] == list(range(1, 21))


@pytest.mark.asyncio
async def test_idempotent_retry_returns_existing_message_no_duplicate(pool):
    suffix = uuid.uuid4().hex[:8]
    async with pool.acquire() as conn:
        from app.repositories import workspaces as workspaces_repo

        workspace = await workspaces_repo.create_workspace(conn, f"Idem {suffix}", f"idem-{suffix}")
        contact = await contacts_repo.create_visitor_contact(conn, workspace["id"], f"visitor-{suffix}")
        conversation = await conversations_repo.create_conversation(conn, workspace["id"], contact["id"], "chat")

        client_msg_id = str(uuid.uuid4())
        first, first_created = await send_message(
            conn, workspace["id"], conversation["id"], sender_type="contact", body="hi", client_msg_id=client_msg_id
        )
        second, second_created = await send_message(
            conn, workspace["id"], conversation["id"], sender_type="contact", body="hi", client_msg_id=client_msg_id
        )

    assert first_created is True
    assert second_created is False
    assert first["id"] == second["id"]
    assert first["seq"] == second["seq"]
