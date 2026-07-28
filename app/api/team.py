import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr

from app.config import settings
from app.db import get_pool
from app.deps import current_membership, require_role
from app.errors import ValidationAppError
from app.repositories import invites as invites_repo
from app.repositories import jobs as jobs_repo
from app.repositories import members as members_repo
from app.services import auth as auth_service

router = APIRouter(prefix="/api/workspaces/{workspace_id}/team", tags=["team"])


class InviteRequest(BaseModel):
    email: EmailStr
    role: str


class RoleUpdateRequest(BaseModel):
    role: str


def _validate_role(role: str) -> None:
    if role not in ("admin", "agent"):
        raise ValidationAppError("role must be admin or agent", code="invalid_role")


@router.get("")
async def list_team(workspace_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        members = await members_repo.list_members(conn, workspace_id)
        pending = await invites_repo.list_pending_invites(conn, workspace_id)
    return {
        "members": [
            {"id": str(m["id"]), "email": m["email"], "name": m["name"], "role": m["role"]} for m in members
        ],
        "pending_invites": [
            {"id": str(i["id"]), "email": i["email"], "role": i["role"], "expires_at": i["expires_at"].isoformat()}
            for i in pending
        ],
    }


@router.post("/invites")
async def invite_member(workspace_id: uuid.UUID, body: InviteRequest, request: Request):
    _validate_role(body.role)
    membership = await current_membership(request, workspace_id)
    require_role(membership, "admin")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            invite, token = await auth_service.create_invite(conn, workspace_id, body.email, body.role, membership.user.id)
            invite_url = f"{settings.app_base_url}/invite/{token}"
            if settings.email_enabled():
                await jobs_repo.enqueue(
                    conn,
                    kind="send_invite_email",
                    payload={"workspace_id": str(workspace_id), "to_email": invite["email"], "invite_url": invite_url},
                    workspace_id=workspace_id,
                )

    # The token is still returned so the link can be shared manually as a fallback,
    # e.g. if SMTP is not configured or the email is delayed.
    return {
        "invite": {"id": str(invite["id"]), "email": invite["email"], "role": invite["role"]},
        "invite_token": token,
        "invite_url": invite_url,
    }


@router.patch("/members/{user_id}")
async def update_member_role(workspace_id: uuid.UUID, user_id: uuid.UUID, body: RoleUpdateRequest, request: Request):
    _validate_role(body.role)
    membership = await current_membership(request, workspace_id)
    require_role(membership, "admin")

    pool = get_pool()
    async with pool.acquire() as conn:
        if body.role != "admin":
            admin_count = await members_repo.count_admins(conn, workspace_id)
            existing = await members_repo.get_membership(conn, workspace_id, user_id)
            if existing and existing["role"] == "admin" and admin_count <= 1:
                raise ValidationAppError("Cannot demote the last admin", code="last_admin")
        await members_repo.update_role(conn, workspace_id, user_id, body.role)

    return {"ok": True}
