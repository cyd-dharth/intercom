from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.config import settings
from app.db import get_pool
from app.deps import SESSION_COOKIE, current_user
from app.ratelimit import check_rate_limit
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_MAX_AGE = 7 * 24 * 3600


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.app_env != "local",
        samesite="lax",
        path="/",
    )


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class AcceptInviteRequest(BaseModel):
    token: str
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=200)


def _user_out(user) -> dict:
    return {"id": str(user["id"]), "email": user["email"], "name": user["name"]}


def _workspace_out(workspace) -> dict:
    return {
        "id": str(workspace["id"]),
        "name": workspace["name"],
        "slug": workspace["slug"],
        "public_key": workspace["public_key"],
    }


@router.post("/signup")
async def signup(body: SignupRequest, request: Request, response: Response):
    check_rate_limit("auth", _client_ip(request))
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user, workspace, token = await auth_service.signup(
                conn, body.email, body.password, body.name, body.workspace_name
            )
    _set_session_cookie(response, token)
    return {"user": _user_out(user), "workspace": _workspace_out(workspace)}


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    check_rate_limit("auth", _client_ip(request))
    pool = get_pool()
    async with pool.acquire() as conn:
        user, token = await auth_service.login(conn, body.email, body.password)
    _set_session_cookie(response, token)
    return {"user": _user_out(user)}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        pool = get_pool()
        async with pool.acquire() as conn:
            await auth_service.logout(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request):
    from app.repositories import members as members_repo

    user = await current_user(request)
    pool = get_pool()
    async with pool.acquire() as conn:
        memberships = await members_repo.list_memberships_for_user(conn, user.id)
    return {
        "user": {"id": str(user.id), "email": user.email, "name": user.name},
        "workspaces": [
            {"id": str(m["id"]), "name": m["name"], "slug": m["slug"], "role": m["role"]} for m in memberships
        ],
    }


@router.post("/invites/accept")
async def accept_invite(body: AcceptInviteRequest, response: Response):
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            user, workspace, token = await auth_service.accept_invite(conn, body.token, body.name, body.password)
    _set_session_cookie(response, token)
    return {"user": _user_out(user), "workspace": _workspace_out(workspace)}
