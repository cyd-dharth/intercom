import re
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from app.errors import ConflictError, UnauthorizedError, ValidationAppError
from app.repositories import invites as invites_repo
from app.repositories import members as members_repo
from app.repositories import sessions as sessions_repo
from app.repositories import users as users_repo
from app.repositories import workspaces as workspaces_repo
from app.security import hash_password, hash_token, new_token, verify_password

SESSION_TTL_DAYS = 7
INVITE_TTL_DAYS = 7

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "workspace"


async def signup(conn: asyncpg.Connection, email: str, password: str, name: str, workspace_name: str) -> tuple[asyncpg.Record, asyncpg.Record, str]:
    existing = await users_repo.get_user_by_email(conn, email)
    if existing is not None:
        raise ConflictError("An account with this email already exists", code="email_taken")

    base_slug = slugify(workspace_name)
    slug = base_slug
    suffix = 1
    while await workspaces_repo.slug_exists(conn, slug):
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    password_hash = hash_password(password)
    user = await users_repo.create_user(conn, email, password_hash, name)
    workspace = await workspaces_repo.create_workspace(conn, workspace_name, slug)
    await members_repo.add_member(conn, workspace["id"], user["id"], "admin")

    token = new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    await sessions_repo.create_session(conn, user["id"], hash_token(token), expires_at)

    return user, workspace, token


async def login(conn: asyncpg.Connection, email: str, password: str) -> tuple[asyncpg.Record, str]:
    user = await users_repo.get_user_by_email(conn, email)
    if user is None or not verify_password(password, user["password_hash"]):
        raise UnauthorizedError("Invalid email or password", code="invalid_credentials")

    token = new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    await sessions_repo.create_session(conn, user["id"], hash_token(token), expires_at)
    return user, token


async def logout(conn: asyncpg.Connection, token: str) -> None:
    await sessions_repo.delete_session(conn, hash_token(token))


async def create_invite(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, email: str, role: str, invited_by: uuid.UUID
) -> tuple[asyncpg.Record, str]:
    token = new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    invite = await invites_repo.create_invite(conn, workspace_id, email, role, hash_token(token), invited_by, expires_at)
    return invite, token


async def accept_invite(conn: asyncpg.Connection, token: str, name: str, password: str) -> tuple[asyncpg.Record, asyncpg.Record, str]:
    invite = await invites_repo.get_invite_by_token_hash(conn, hash_token(token))
    if invite is None:
        raise ValidationAppError("Invite not found", code="invite_invalid")
    if invite["accepted_at"] is not None:
        raise ConflictError("Invite already accepted", code="invite_used")
    if invite["expires_at"] < datetime.now(timezone.utc):
        raise ValidationAppError("Invite has expired", code="invite_expired")

    user = await users_repo.get_user_by_email(conn, invite["email"])
    if user is None:
        password_hash = hash_password(password)
        user = await users_repo.create_user(conn, invite["email"], password_hash, name)

    await members_repo.add_member(conn, invite["workspace_id"], user["id"], invite["role"])
    await invites_repo.mark_accepted(conn, invite["id"])

    workspace = await workspaces_repo.get_workspace_by_id(conn, invite["workspace_id"])

    session_token = new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    await sessions_repo.create_session(conn, user["id"], hash_token(session_token), expires_at)

    return user, workspace, session_token
