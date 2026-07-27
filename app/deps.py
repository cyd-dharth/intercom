import uuid
from dataclasses import dataclass

from fastapi import Request

from app.db import get_pool
from app.errors import ForbiddenError, UnauthorizedError
from app.repositories import members as members_repo
from app.repositories import sessions as sessions_repo
from app.repositories import users as users_repo
from app.security import hash_token

SESSION_COOKIE = "session"


@dataclass
class CurrentUser:
    id: uuid.UUID
    email: str
    name: str


@dataclass
class CurrentMembership:
    user: CurrentUser
    workspace_id: uuid.UUID
    role: str


async def current_user(request: Request) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise UnauthorizedError("Not authenticated")
    pool = get_pool()
    async with pool.acquire() as conn:
        session = await sessions_repo.get_session_by_token_hash(conn, hash_token(token))
        if session is None:
            raise UnauthorizedError("Session expired or invalid")
        user = await users_repo.get_user_by_id(conn, session["user_id"])
        if user is None:
            raise UnauthorizedError("User not found")
    return CurrentUser(id=user["id"], email=user["email"], name=user["name"])


async def current_membership(request: Request, workspace_id: uuid.UUID) -> CurrentMembership:
    user = await current_user(request)
    pool = get_pool()
    async with pool.acquire() as conn:
        membership = await members_repo.get_membership(conn, workspace_id, user.id)
    if membership is None:
        raise ForbiddenError("Not a member of this workspace")
    return CurrentMembership(user=user, workspace_id=workspace_id, role=membership["role"])


def require_role(membership: CurrentMembership, *roles: str) -> None:
    if membership.role not in roles:
        raise ForbiddenError("Insufficient role for this action")
