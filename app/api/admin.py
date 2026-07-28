import uuid

from fastapi import APIRouter, Request

from app.db import get_pool
from app.deps import current_membership
from app.repositories import ai as ai_repo
from app.repositories import jobs as jobs_repo
from app.repositories import workspaces as workspaces_repo

router = APIRouter(prefix="/api/workspaces/{workspace_id}/admin", tags=["admin"])


@router.get("/jobs")
async def job_counts(workspace_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await jobs_repo.counts_by_kind_status(conn)
        pending = await jobs_repo.count_pending(conn)
    return {
        "pending_total": pending,
        "by_kind_status": [{"kind": r["kind"], "status": r["status"], "count": r["c"]} for r in rows],
    }


@router.get("/ai-usage")
async def ai_usage(workspace_id: uuid.UUID, request: Request):
    """Visible spend and status breakdown per CLAUDE.md section 10.4: a real number
    beats a paragraph claiming cost awareness."""
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_id(conn, workspace_id)
        calls = await ai_repo.list_calls_today(conn, workspace_id)

    total_cost_micros = sum(c["cost_micros"] for c in calls)
    total_input_tokens = sum(c["input_tokens"] or 0 for c in calls)
    total_output_tokens = sum(c["output_tokens"] or 0 for c in calls)
    status_breakdown: dict[str, int] = {}
    for call in calls:
        status_breakdown[call["status"]] = status_breakdown.get(call["status"], 0) + 1

    return {
        "daily_budget_cents": workspace["ai_daily_budget_cents"] if workspace else None,
        "spent_cents_today": total_cost_micros / 10_000,
        "call_count_today": len(calls),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "status_breakdown": status_breakdown,
        "calls": [
            {
                "id": c["id"],
                "kind": c["kind"],
                "model": c["model"],
                "prompt_version": c["prompt_version"],
                "input_tokens": c["input_tokens"],
                "output_tokens": c["output_tokens"],
                "cost_micros": c["cost_micros"],
                "latency_ms": c["latency_ms"],
                "status": c["status"],
                "created_at": c["created_at"].isoformat(),
            }
            for c in calls
        ],
    }
