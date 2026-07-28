import re
import uuid

import asyncpg
import nh3

from app.repositories import jobs as jobs_repo
from app.repositories import kb as kb_repo

MAX_CHUNK_CHARS = 1600
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

ARTICLE_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "b", "i", "ul", "ol", "li", "h1", "h2", "h3", "h4",
    "h5", "h6", "blockquote", "code", "pre", "a", "hr", "table", "thead", "tbody",
    "tr", "th", "td",
}


def sanitize_html(html: str) -> str:
    """The one real XSS surface per CLAUDE.md section 15: KB markdown is rendered to
    HTML server side then sanitised with an element allowlist before ever reaching a
    browser, whether the dashboard preview or a public KB page."""
    return nh3.clean(html, tags=ARTICLE_ALLOWED_TAGS)


def _split_sections(body_md: str) -> list[tuple[str | None, str]]:
    matches = list(HEADING_RE.finditer(body_md))
    if not matches:
        return [(None, body_md)] if body_md.strip() else []
    sections = []
    if matches[0].start() > 0:
        preamble = body_md[: matches[0].start()].strip()
        if preamble:
            sections.append((None, preamble))
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body_md)
        content = body_md[start:end].strip()
        if content:
            sections.append((heading, content))
    return sections


def _pack_section(heading: str | None, content: str) -> list[str]:
    """Packs a section's sentences to roughly MAX_CHUNK_CHARS with a 1 sentence overlap
    between consecutive chunks of the same section, per section 10.3."""
    sentences = [s.strip() for s in SENTENCE_RE.split(content) if s.strip()]
    if not sentences:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        if current and current_len + len(sentence) > MAX_CHUNK_CHARS:
            chunks.append(" ".join(current))
            current = [current[-1]] if current else []
            current_len = len(current[0]) if current else 0
        current.append(sentence)
        current_len += len(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def build_chunks(title: str, body_md: str) -> list[dict]:
    """Chunking on publish per section 10.3: split on markdown headings, pack to
    roughly 400 tokens with sentence overlap, prepend a self describing header to
    every chunk so it stands alone in a search result."""
    sections = _split_sections(body_md)
    chunks = []
    index = 0
    for heading, content in sections:
        for piece in _pack_section(heading, content):
            prefix = f"{title} > {heading}" if heading else title
            chunks.append({
                "chunk_index": index,
                "heading": heading,
                "content": f"{prefix}\n{piece}",
            })
            index += 1
    return chunks


async def publish_article(conn: asyncpg.Connection, workspace_id: uuid.UUID, article_id: uuid.UUID) -> asyncpg.Record | None:
    article = await kb_repo.set_article_status(conn, workspace_id, article_id, "published")
    if article is None:
        return None
    chunks = build_chunks(article["title"], article["body_md"])
    await kb_repo.replace_chunks(conn, workspace_id, article_id, chunks)
    if chunks:
        await jobs_repo.enqueue(
            conn,
            kind="embed_article",
            payload={"workspace_id": str(workspace_id), "article_id": str(article_id)},
            workspace_id=workspace_id,
            dedupe_key=f"embed_article:{article_id}",
        )
    return article
