import logging
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.ai.retrieval import hybrid_search
from app.db import get_pool
from app.errors import UnauthorizedError, ValidationAppError
from app.ratelimit import check_rate_limit
from app.repositories import contacts as contacts_repo
from app.repositories import messages as messages_repo
from app.repositories import workspaces as workspaces_repo
from app.security import issue_visitor_token, verify_visitor_token
from app.services.conversations import get_or_create_visitor_conversation

logger = logging.getLogger("app.api.widget")

router = APIRouter(prefix="/api/widget", tags=["widget"])


class WidgetSessionRequest(BaseModel):
    public_key: str = Field(min_length=1, max_length=200)
    visitor_id: str = Field(min_length=1, max_length=200)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _origin_allowed(origin: str | None, allowed_origins: list[str]) -> bool:
    if not allowed_origins:
        return True
    if origin is None:
        return False
    return origin in allowed_origins


@router.post("/session")
async def widget_session(body: WidgetSessionRequest, request: Request):
    check_rate_limit("widget_session", _client_ip(request))

    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_public_key(conn, body.public_key)
        if workspace is None:
            raise ValidationAppError("Unknown public key", code="invalid_public_key")

        origin = request.headers.get("origin")
        if not _origin_allowed(origin, workspace["allowed_origins"]):
            logger.warning("widget origin rejected", extra={"extra_fields": {"workspace_id": str(workspace["id"])}})
            raise UnauthorizedError("Origin not allowed for this workspace", code="origin_not_allowed")

        async with conn.transaction():
            contact = await contacts_repo.create_visitor_contact(conn, workspace["id"], body.visitor_id)
            conversation = await get_or_create_visitor_conversation(conn, workspace["id"], contact["id"])

        recent_messages = await messages_repo.list_recent(conn, workspace["id"], conversation["id"], limit=50)

    token = issue_visitor_token(workspace["id"], contact["id"])

    return {
        "visitor_token": token,
        "workspace": {"id": str(workspace["id"]), "name": workspace["name"], "slug": workspace["slug"]},
        "conversation": {
            "id": str(conversation["id"]),
            "status": conversation["status"],
            "last_seq": conversation["last_seq"],
        },
        "messages": [
            {
                "id": str(m["id"]),
                "seq": m["seq"],
                "sender_type": m["sender_type"],
                "body": m["body"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in recent_messages
        ],
    }


class SuggestRequest(BaseModel):
    q: str = Field(min_length=1, max_length=500)


@router.post("/suggest")
async def widget_suggest(body: SuggestRequest, request: Request):
    """KB auto suggest inside the widget per section 5 feature 5 and section 10.3.
    Never blocks sending a message: the frontend debounces this call and ignores
    failures, this endpoint just runs the same hybrid_search used by the public KB
    search page."""
    check_rate_limit("kb_search", _client_ip(request))
    visitor = current_visitor(request)
    pool = get_pool()
    async with pool.acquire() as conn:
        results = await hybrid_search(conn, uuid.UUID(visitor["workspace_id"]), body.q)
    return {"results": results}


def current_visitor(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else request.query_params.get("token")
    if not token:
        raise UnauthorizedError("Missing visitor token")
    payload = verify_visitor_token(token)
    if payload is None:
        raise UnauthorizedError("Invalid or expired visitor token")
    return payload
