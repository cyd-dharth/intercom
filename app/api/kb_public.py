import uuid

import markdown as markdown_lib
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.db import get_pool
from app.errors import NotFoundError, ValidationAppError
from app.ratelimit import check_rate_limit
from app.ai.retrieval import hybrid_search
from app.repositories import kb as kb_repo
from app.repositories import workspaces as workspaces_repo
from app.services.kb import sanitize_html

router = APIRouter(tags=["kb_public"])
templates = Jinja2Templates(directory="app/templates")


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=500)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/kb/public/{workspace_slug}", response_class=HTMLResponse)
async def kb_index(request: Request, workspace_slug: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_slug(conn, workspace_slug)
        if workspace is None:
            raise NotFoundError("Knowledge base not found")
        categories = await kb_repo.list_categories(conn, workspace["id"])
        categories_with_articles = []
        for category in categories:
            articles = await kb_repo.list_published_by_category(conn, workspace["id"], category["id"])
            categories_with_articles.append({"category": category, "articles": articles})
    return templates.TemplateResponse(
        "kb_index.html",
        {
            "request": request,
            "workspace": workspace,
            "workspace_slug": workspace_slug,
            "categories_with_articles": categories_with_articles,
        },
    )


@router.get("/kb/public/{workspace_slug}/{article_slug}", response_class=HTMLResponse)
async def kb_article(request: Request, workspace_slug: str, article_slug: str):
    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_slug(conn, workspace_slug)
        if workspace is None:
            raise NotFoundError("Knowledge base not found")
        article = await kb_repo.get_article_by_slug(conn, workspace["id"], article_slug)
        if article is None or article["status"] != "published":
            raise NotFoundError("Article not found")
    html = sanitize_html(markdown_lib.markdown(article["body_md"], extensions=["fenced_code", "tables"]))
    return templates.TemplateResponse(
        "kb_article.html",
        {"request": request, "workspace": workspace, "workspace_slug": workspace_slug, "article": article, "article_html": html},
    )


@router.get("/kb/public/{workspace_slug}/search/results", response_class=HTMLResponse)
async def kb_search_page(request: Request, workspace_slug: str, q: str = ""):
    check_rate_limit("kb_search", _client_ip(request))
    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_slug(conn, workspace_slug)
        if workspace is None:
            raise NotFoundError("Knowledge base not found")
        results = await hybrid_search(conn, workspace["id"], q) if q else []
    return templates.TemplateResponse(
        "kb_search.html",
        {"request": request, "workspace": workspace, "workspace_slug": workspace_slug, "query": q, "results": results},
    )


@router.post("/api/kb/public/{workspace_slug}/search")
async def kb_search_api(request: Request, workspace_slug: str, body: SearchRequest):
    """Same hybrid_search function that powers the public search page and the widget
    auto suggest panel, per CLAUDE.md section 10.3: one code path, two surfaces."""
    check_rate_limit("kb_search", _client_ip(request))
    pool = get_pool()
    async with pool.acquire() as conn:
        workspace = await workspaces_repo.get_workspace_by_slug(conn, workspace_slug)
        if workspace is None:
            raise NotFoundError("Knowledge base not found")
        results = await hybrid_search(conn, workspace["id"], body.q)
    return {"results": results}
