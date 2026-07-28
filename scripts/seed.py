"""Seeds a demo workspace so an evaluator sees a populated product on first load,
per CLAUDE.md section 14 and the phase 5 acceptance check in section 16. Safe to
run more than once: it looks up the demo workspace by its fixed slug and skips
creating it again, only topping up if articles or conversations are missing.

Usage: python -m scripts.seed
"""

import asyncio
import logging

import asyncpg

from app.config import settings
from app.db import _init_connection
from app.repositories import contacts as contacts_repo
from app.repositories import conversations as conversations_repo
from app.repositories import kb as kb_repo
from app.repositories import members as members_repo
from app.repositories import users as users_repo
from app.repositories import workspaces as workspaces_repo
from app.security import hash_password
from app.services.conversations import send_message
from app.services.kb import publish_article

logger = logging.getLogger("app.seed")

DEMO_SLUG = "demo"
DEMO_WORKSPACE_NAME = "Acme Demo"
DEMO_ADMIN_EMAIL = "admin@demo.example"
DEMO_ADMIN_PASSWORD = "demopass123"
DEMO_ADMIN_NAME = "Demo Admin"

ARTICLES = [
    {
        "category": "Getting started",
        "title": "Creating your first project",
        "body_md": (
            "# Creating your first project\n\n"
            "Every account starts with one workspace. To create a project, open the "
            "dashboard and click New Project in the top left.\n\n"
            "## Naming your project\n\n"
            "Project names must be unique within your workspace and under 60 characters. "
            "You can rename a project at any time from its settings page.\n\n"
            "## Inviting teammates\n\n"
            "Projects inherit the members of the workspace they belong to. Add teammates "
            "from Settings > Team before creating a project if you want them to see it "
            "immediately."
        ),
    },
    {
        "category": "Getting started",
        "title": "Understanding workspace roles",
        "body_md": (
            "# Understanding workspace roles\n\n"
            "Every member of a workspace is either an admin or an agent.\n\n"
            "## Admins\n\n"
            "Admins can invite and remove teammates, change roles, add custom domains, "
            "and see AI spend on the Settings page.\n\n"
            "## Agents\n\n"
            "Agents can see and respond to every conversation in the shared inbox, "
            "write and publish knowledge base articles, but cannot manage billing or "
            "team membership."
        ),
    },
    {
        "category": "Billing",
        "title": "Understanding your invoice",
        "body_md": (
            "# Understanding your invoice\n\n"
            "Invoices are generated on the first of each month and cover the previous "
            "30 days of usage.\n\n"
            "## Line items\n\n"
            "Each invoice breaks usage down by seats, AI credits consumed, and any "
            "custom domain add ons.\n\n"
            "## Failed payments\n\n"
            "If a card is declined we retry three times over five days before pausing "
            "the workspace. You will get an email before anything is paused."
        ),
    },
    {
        "category": "Billing",
        "title": "Changing your plan",
        "body_md": (
            "# Changing your plan\n\n"
            "You can upgrade or downgrade at any time from Settings > Billing.\n\n"
            "## Upgrading\n\n"
            "Upgrades apply immediately and you are charged a prorated amount for the "
            "rest of the current billing cycle.\n\n"
            "## Downgrading\n\n"
            "Downgrades take effect at the start of the next billing cycle so you keep "
            "access to your current plan's features until then."
        ),
    },
    {
        "category": "Troubleshooting",
        "title": "The widget is not appearing on my site",
        "body_md": (
            "# The widget is not appearing on my site\n\n"
            "If the chat launcher button is not showing up, check the following in order.\n\n"
            "## Script tag placement\n\n"
            "The widget script tag must be present in the page HTML, not injected after "
            "load by another script that itself waits on an event.\n\n"
            "## Allowed origins\n\n"
            "If your workspace has any allowed origins configured, the widget will "
            "silently refuse to start a session from an origin that is not on the list. "
            "Add your site's origin in Settings.\n\n"
            "## Ad blockers\n\n"
            "Some aggressive ad and tracker blockers remove iframes whose src contains "
            "the word widget. This is rare but worth checking if a small number of "
            "visitors report the issue."
        ),
    },
    {
        "category": "Troubleshooting",
        "title": "My email replies are not threading correctly",
        "body_md": (
            "# My email replies are not threading correctly\n\n"
            "Threading is resolved using a chain of fallbacks, in order.\n\n"
            "## Reply headers\n\n"
            "Most mail clients preserve the In-Reply-To and References headers "
            "automatically, which is the most reliable signal we use.\n\n"
            "## Reply-To address\n\n"
            "Every outbound message sets a unique Reply-To address that encodes the "
            "conversation, which survives even if a client strips the other headers.\n\n"
            "## Subject matching\n\n"
            "As a last resort we match on a normalised subject line and sender address "
            "within a 30 day window. Changing the subject line of a reply can break this "
            "fallback, so we recommend leaving it as is."
        ),
    },
]

CONVERSATION_SCRIPTS = [
    {
        "contact_name": "Priya Nair",
        "contact_email": "priya@example.com",
        "messages": [
            ("contact", "Hi, I can't find where to invite my teammate."),
            ("agent", "Hi Priya, you can invite teammates from Settings > Team, then Invite by email."),
            ("contact", "Found it, thanks!"),
        ],
    },
    {
        "contact_name": "Jordan Lee",
        "contact_email": "jordan@example.com",
        "messages": [
            ("contact", "The chat widget isn't showing up on our marketing site."),
            ("agent", "Sorry about that. Can you tell me which domain you're testing on?"),
            ("contact", "www.example.com"),
            ("agent", "That domain isn't in your workspace's allowed origins list yet, that's likely it."),
            ("contact", "Added it, let me check."),
            ("contact", "That fixed it, the launcher button showed up right away."),
        ],
    },
    {
        "contact_name": "Sam Okafor",
        "contact_email": "sam@example.com",
        "messages": [
            ("contact", "Our email replies keep creating new conversations instead of continuing the thread."),
            ("agent", "That usually means the reply headers are being stripped somewhere. What mail client are you replying from?"),
            ("contact", "Outlook desktop, corporate account."),
            ("agent", "Some corporate Outlook setups strip In-Reply-To on send. Can you check if the Reply-To address on our emails looks like yourname+c1a2b3c4.something@yourdomain?"),
            ("contact", "Yes I see that address in the Reply-To field."),
            ("agent", "Good, that should still resolve the thread correctly even without the headers. Can you try replying again and let me know the conversation you land in?"),
            ("contact", "Just replied, and it landed in this same conversation this time."),
            ("agent", "That confirms it, the Reply-To fallback is working as expected on your end."),
            ("contact", "One more question, does this also work if I forward instead of reply?"),
            ("agent", "Forwarding generates a new Message-ID with no relation to the original thread, so no, a forward will always start a new conversation."),
            ("contact", "That makes sense, thanks for digging into this."),
            ("agent", "Happy to help. I'll mark this resolved, feel free to reopen if it happens again."),
            ("contact", "Actually, one more thing, can multiple people reply into the same thread?"),
            ("agent", "Yes, anyone replying to the same message or using the same Reply-To address lands in this conversation, regardless of who they are."),
        ],
    },
]


async def _get_or_create_admin(conn: asyncpg.Connection):
    admin = await users_repo.get_user_by_email(conn, DEMO_ADMIN_EMAIL)
    if admin is None:
        admin = await users_repo.create_user(conn, DEMO_ADMIN_EMAIL, hash_password(DEMO_ADMIN_PASSWORD), DEMO_ADMIN_NAME)
    return admin


async def _get_or_create_workspace(conn: asyncpg.Connection):
    workspace = await workspaces_repo.get_workspace_by_slug(conn, DEMO_SLUG)
    if workspace is None:
        workspace = await workspaces_repo.create_workspace(conn, DEMO_WORKSPACE_NAME, DEMO_SLUG)
    return workspace


async def _seed_articles(conn: asyncpg.Connection, workspace_id) -> None:
    existing = await kb_repo.list_articles(conn, workspace_id)
    if len(existing) >= len(ARTICLES):
        logger.info("kb articles already seeded, skipping")
        return

    categories: dict[str, object] = {}
    for spec in ARTICLES:
        category_name = spec["category"]
        if category_name not in categories:
            slug = category_name.lower().replace(" ", "-")
            category = await kb_repo.get_category_by_slug(conn, workspace_id, slug)
            if category is None:
                category = await kb_repo.create_category(conn, workspace_id, category_name, slug)
            categories[category_name] = category

        slug = spec["title"].lower().replace(" ", "-").replace("'", "")
        existing_article = await kb_repo.get_article_by_slug(conn, workspace_id, slug)
        if existing_article is not None:
            continue
        article = await kb_repo.create_article(
            conn, workspace_id, spec["title"], slug, spec["body_md"], categories[category_name]["id"]
        )
        await publish_article(conn, workspace_id, article["id"])
        logger.info("seeded article: %s", spec["title"])


async def _seed_conversations(conn: asyncpg.Connection, workspace_id, admin_id) -> None:
    existing = await conversations_repo.list_inbox(conn, workspace_id, limit=1)
    if existing:
        logger.info("conversations already seeded, skipping")
        return

    for script in CONVERSATION_SCRIPTS:
        contact = await contacts_repo.create_email_contact(conn, workspace_id, script["contact_email"], script["contact_name"])
        conversation = await conversations_repo.create_conversation(conn, workspace_id, contact["id"], "email", subject="Support request")
        for sender_type, body in script["messages"]:
            sender_user_id = admin_id if sender_type == "agent" else None
            await send_message(conn, workspace_id, conversation["id"], sender_type=sender_type, body=body, sender_user_id=sender_user_id)
        logger.info("seeded conversation with %s (%d messages)", script["contact_name"], len(script["messages"]))


async def seed(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        admin = await _get_or_create_admin(conn)
        workspace = await _get_or_create_workspace(conn)
        await members_repo.add_member(conn, workspace["id"], admin["id"], "admin")
        await _seed_articles(conn, workspace["id"])
        await _seed_conversations(conn, workspace["id"], admin["id"])
    logger.info("seed complete: workspace slug=%s admin email=%s", DEMO_SLUG, DEMO_ADMIN_EMAIL)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=2, statement_cache_size=0, init=_init_connection)
    try:
        await seed(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
