"""Tenant isolation tests per CLAUDE.md section 6.3.

Creates two workspaces, each with a contact, a conversation, a message, and a
published KB article, then asserts that every repository read for workspace A's
data, when called with workspace B's id, returns nothing. Every repository
function takes workspace_id as an explicit argument (rule 5); these tests are
the proof that argument is load bearing, not decorative.

Run against the real database configured in DATABASE_URL (see .env), same as
scripts/migrate.py. Requires the schema to already be applied.
"""

import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.config import settings
from app.db import _init_connection
from app.repositories import contacts as contacts_repo
from app.repositories import conversations as conversations_repo
from app.repositories import kb as kb_repo
from app.repositories import messages as messages_repo
from app.repositories import workspaces as workspaces_repo
from app.services.conversations import send_message
from app.services.kb import publish_article


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=2, statement_cache_size=0, init=_init_connection)
    yield p
    await p.close()


class Tenant:
    def __init__(self, workspace, contact, conversation, message, article):
        self.workspace = workspace
        self.workspace_id = workspace["id"]
        self.contact = contact
        self.contact_id = contact["id"]
        self.conversation = conversation
        self.conversation_id = conversation["id"]
        self.message = message
        self.message_id = message["id"]
        self.article = article
        self.article_id = article["id"]


async def _make_tenant(conn: asyncpg.Connection, label: str) -> Tenant:
    suffix = uuid.uuid4().hex[:8]
    workspace = await workspaces_repo.create_workspace(conn, f"Isolation {label} {suffix}", f"iso-{label}-{suffix}")
    contact = await contacts_repo.create_visitor_contact(conn, workspace["id"], f"visitor-{suffix}")
    conversation = await conversations_repo.create_conversation(conn, workspace["id"], contact["id"], "chat")
    message, _ = await send_message(
        conn,
        workspace["id"],
        conversation["id"],
        sender_type="contact",
        body=f"hello from {label}",
    )
    article = await kb_repo.create_article(conn, workspace["id"], f"{label} article", f"{label}-article-{suffix}", "# heading\n\nsome body text", None)
    article = await publish_article(conn, workspace["id"], article["id"])
    return Tenant(workspace, contact, conversation, message, article)


@pytest_asyncio.fixture
async def tenants(pool):
    async with pool.acquire() as conn:
        a = await _make_tenant(conn, "a")
        b = await _make_tenant(conn, "b")
    return a, b


@pytest.mark.asyncio
async def test_conversations_are_tenant_isolated(pool, tenants):
    a, b = tenants
    async with pool.acquire() as conn:
        assert await conversations_repo.get_by_id(conn, b.workspace_id, a.conversation_id) is None
        assert await conversations_repo.get_by_id(conn, a.workspace_id, a.conversation_id) is not None

        rows = await conversations_repo.list_inbox(conn, b.workspace_id)
        assert all(r["id"] != a.conversation_id for r in rows)


@pytest.mark.asyncio
async def test_messages_are_tenant_isolated(pool, tenants):
    a, b = tenants
    async with pool.acquire() as conn:
        assert await messages_repo.get_by_id(conn, b.workspace_id, a.message_id) is None
        assert await messages_repo.get_by_id(conn, a.workspace_id, a.message_id) is not None

        cross_tenant_messages = await messages_repo.list_recent(conn, b.workspace_id, a.conversation_id)
        assert cross_tenant_messages == []

        same_tenant_messages = await messages_repo.list_recent(conn, a.workspace_id, a.conversation_id)
        assert len(same_tenant_messages) == 1


@pytest.mark.asyncio
async def test_articles_are_tenant_isolated(pool, tenants):
    a, b = tenants
    async with pool.acquire() as conn:
        assert await kb_repo.get_article_by_id(conn, b.workspace_id, a.article_id) is None
        assert await kb_repo.get_article_by_id(conn, a.workspace_id, a.article_id) is not None

        assert await kb_repo.get_article_by_slug(conn, b.workspace_id, a.article["slug"]) is None

        cross_tenant_articles = await kb_repo.list_articles(conn, b.workspace_id)
        assert all(r["id"] != a.article_id for r in cross_tenant_articles)

        cross_tenant_chunks = await kb_repo.lexical_search(conn, b.workspace_id, "heading")
        assert all(c["article_id"] != a.article_id for c in cross_tenant_chunks)


@pytest.mark.asyncio
async def test_contacts_are_tenant_isolated(pool, tenants):
    a, b = tenants
    async with pool.acquire() as conn:
        assert await contacts_repo.get_by_id(conn, b.workspace_id, a.contact_id) is None
        assert await contacts_repo.get_by_id(conn, a.workspace_id, a.contact_id) is not None

        assert await contacts_repo.get_by_visitor_id(conn, b.workspace_id, a.contact["visitor_id"]) is None
