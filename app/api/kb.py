import re
import uuid

import markdown as markdown_lib
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.db import get_pool
from app.deps import current_membership
from app.errors import NotFoundError, ValidationAppError
from app.repositories import kb as kb_repo
from app.services.kb import publish_article, sanitize_html

router = APIRouter(prefix="/api/workspaces/{workspace_id}/kb", tags=["kb"])

SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def _category_out(c) -> dict:
    return {"id": str(c["id"]), "name": c["name"], "slug": c["slug"], "position": c["position"]}


def _article_out(a) -> dict:
    return {
        "id": str(a["id"]),
        "category_id": str(a["category_id"]) if a["category_id"] else None,
        "title": a["title"],
        "slug": a["slug"],
        "body_md": a["body_md"],
        "status": a["status"],
        "published_at": a["published_at"].isoformat() if a["published_at"] else None,
        "updated_at": a["updated_at"].isoformat(),
    }


class CreateCategoryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CreateArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body_md: str = Field(default="", max_length=100000)
    category_id: uuid.UUID | None = None


class UpdateArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body_md: str = Field(default="", max_length=100000)
    category_id: uuid.UUID | None = None


@router.get("/categories")
async def list_categories(workspace_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await kb_repo.list_categories(conn, workspace_id)
    return {"categories": [_category_out(c) for c in rows]}


@router.post("/categories")
async def create_category(workspace_id: uuid.UUID, body: CreateCategoryRequest, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        category = await kb_repo.create_category(conn, workspace_id, body.name, _slugify(body.name))
    return {"category": _category_out(category)}


@router.get("/articles")
async def list_articles(workspace_id: uuid.UUID, request: Request, status: str | None = None):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await kb_repo.list_articles(conn, workspace_id, status=status)
    return {"articles": [_article_out(a) for a in rows]}


@router.get("/articles/{article_id}")
async def get_article(workspace_id: uuid.UUID, article_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        article = await kb_repo.get_article_by_id(conn, workspace_id, article_id)
    if article is None:
        raise NotFoundError("Article not found")
    return {"article": _article_out(article)}


@router.post("/articles")
async def create_article(workspace_id: uuid.UUID, body: CreateArticleRequest, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        article = await kb_repo.create_article(conn, workspace_id, body.title, _slugify(body.title), body.body_md, body.category_id)
    return {"article": _article_out(article)}


@router.patch("/articles/{article_id}")
async def update_article(workspace_id: uuid.UUID, article_id: uuid.UUID, body: UpdateArticleRequest, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        article = await kb_repo.update_article(conn, workspace_id, article_id, body.title, body.body_md, body.category_id)
        if article is None:
            raise NotFoundError("Article not found")
        if article["status"] == "published":
            article = await publish_article(conn, workspace_id, article_id)
    return {"article": _article_out(article)}


@router.post("/articles/{article_id}/publish")
async def publish(workspace_id: uuid.UUID, article_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        article = await publish_article(conn, workspace_id, article_id)
    if article is None:
        raise NotFoundError("Article not found")
    return {"article": _article_out(article)}


@router.post("/articles/{article_id}/unpublish")
async def unpublish(workspace_id: uuid.UUID, article_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    pool = get_pool()
    async with pool.acquire() as conn:
        article = await kb_repo.set_article_status(conn, workspace_id, article_id, "draft")
    if article is None:
        raise NotFoundError("Article not found")
    return {"article": _article_out(article)}


@router.post("/preview")
async def preview_markdown(workspace_id: uuid.UUID, request: Request):
    await current_membership(request, workspace_id)
    body = await request.json()
    body_md = body.get("body_md", "")
    if len(body_md) > 100000:
        raise ValidationAppError("Body too long", code="body_too_long")
    html = markdown_lib.markdown(body_md, extensions=["fenced_code", "tables"])
    return {"html": sanitize_html(html)}
