import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.db import get_pool
from app.deps import current_membership
from app.errors import ConflictError, NotFoundError, ValidationAppError
from app.repositories import domains as domains_repo
from app.repositories import jobs as jobs_repo
from app.services.domains import dns_records_for, new_verification_token

router = APIRouter(prefix="/api/workspaces/{workspace_id}/domains", tags=["domains"])


class CreateDomainRequest(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)


def _domain_out(d) -> dict:
    return {
        "id": str(d["id"]),
        "hostname": d["hostname"],
        "status": d["status"],
        "last_checked_at": d["last_checked_at"].isoformat() if d["last_checked_at"] else None,
        "last_error": d["last_error"],
        "created_at": d["created_at"].isoformat(),
        "dns_records": dns_records_for(d["hostname"], d["verification_token"]),
    }


def _normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


@router.get("")
async def list_domains(workspace_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await domains_repo.list_domains(conn, workspace_id)
    return {"domains": [_domain_out(d) for d in rows]}


@router.post("")
async def create_domain(workspace_id: uuid.UUID, body: CreateDomainRequest, request: Request):
    await current_membership(request, workspace_id)
    hostname = _normalize_hostname(body.hostname)
    if not hostname or "." not in hostname:
        raise ValidationAppError("Enter a valid hostname", code="invalid_hostname")

    pool = get_pool()
    async with pool.acquire() as conn:
        if await domains_repo.hostname_exists(conn, hostname):
            raise ConflictError("This hostname is already registered", code="hostname_taken")
        async with conn.transaction():
            domain = await domains_repo.create_domain(conn, workspace_id, hostname, new_verification_token())
            await jobs_repo.enqueue(
                conn,
                kind="verify_domain",
                payload={"workspace_id": str(workspace_id), "domain_id": str(domain["id"]), "attempt": 1},
                workspace_id=workspace_id,
                dedupe_key=f"verify_domain:{domain['id']}:1",
            )
    return {"domain": _domain_out(domain)}


@router.post("/{domain_id}/check")
async def check_domain_now(workspace_id: uuid.UUID, domain_id: uuid.UUID, request: Request):
    """The Settings page "Check now" button per section 12 step 4. Enqueues an
    immediate re-check rather than blocking the request on a live DNS lookup, keeping
    slow or hanging resolvers out of the request path. Uses its own dedupe key rather
    than the auto retry chain's key, so a manual check while an auto retry is already
    pending runs alongside it instead of being silently dropped; both are harmless
    since a verified result from either one short circuits the other on its next run."""
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        domain = await domains_repo.get_by_id(conn, workspace_id, domain_id)
        if domain is None:
            raise NotFoundError("Domain not found")
        job = await jobs_repo.enqueue(
            conn,
            kind="verify_domain",
            payload={"workspace_id": str(workspace_id), "domain_id": str(domain_id), "attempt": 1},
            workspace_id=workspace_id,
            dedupe_key=f"verify_domain_manual:{domain_id}",
        )
    return {"status": "enqueued" if job else "already_pending", "domain": _domain_out(domain)}
