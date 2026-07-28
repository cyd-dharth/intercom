import uuid

import asyncpg


async def list_categories(conn: asyncpg.Connection, workspace_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, workspace_id, name, slug, position
          from kb_categories
         where workspace_id = $1
         order by position asc, name asc
        """,
        workspace_id,
    )


async def create_category(conn: asyncpg.Connection, workspace_id: uuid.UUID, name: str, slug: str) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into kb_categories (workspace_id, name, slug)
        values ($1, $2, $3)
        returning id, workspace_id, name, slug, position
        """,
        workspace_id,
        name,
        slug,
    )


async def get_category_by_slug(conn: asyncpg.Connection, workspace_id: uuid.UUID, slug: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, name, slug, position
          from kb_categories
         where workspace_id = $1 and slug = $2
        """,
        workspace_id,
        slug,
    )


async def list_articles(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, status: str | None = None
) -> list[asyncpg.Record]:
    conditions = ["workspace_id = $1"]
    params: list = [workspace_id]
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    where_clause = " and ".join(conditions)
    return await conn.fetch(
        f"""
        select id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
          from kb_articles
         where {where_clause}
         order by updated_at desc
        """,
        *params,
    )


async def list_published_by_category(conn: asyncpg.Connection, workspace_id: uuid.UUID, category_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
          from kb_articles
         where workspace_id = $1 and category_id = $2 and status = 'published'
         order by title asc
        """,
        workspace_id,
        category_id,
    )


async def get_article_by_id(conn: asyncpg.Connection, workspace_id: uuid.UUID, article_id: uuid.UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
          from kb_articles
         where workspace_id = $1 and id = $2
        """,
        workspace_id,
        article_id,
    )


async def get_article_by_slug(conn: asyncpg.Connection, workspace_id: uuid.UUID, slug: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        select id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
          from kb_articles
         where workspace_id = $1 and slug = $2
        """,
        workspace_id,
        slug,
    )


async def create_article(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    title: str,
    slug: str,
    body_md: str,
    category_id: uuid.UUID | None,
) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        insert into kb_articles (workspace_id, title, slug, body_md, category_id)
        values ($1, $2, $3, $4, $5)
        returning id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
        """,
        workspace_id,
        title,
        slug,
        body_md,
        category_id,
    )


async def update_article(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    article_id: uuid.UUID,
    title: str,
    body_md: str,
    category_id: uuid.UUID | None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update kb_articles
           set title = $3, body_md = $4, category_id = $5, updated_at = now()
         where workspace_id = $1 and id = $2
        returning id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
        """,
        workspace_id,
        article_id,
        title,
        body_md,
        category_id,
    )


async def set_article_status(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, article_id: uuid.UUID, status: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        update kb_articles
           set status = $3,
               published_at = case when $3 = 'published' then now() else published_at end,
               updated_at = now()
         where workspace_id = $1 and id = $2
        returning id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
        """,
        workspace_id,
        article_id,
        status,
    )


async def replace_chunks(
    conn: asyncpg.Connection,
    workspace_id: uuid.UUID,
    article_id: uuid.UUID,
    chunks: list[dict],
) -> None:
    """Deletes and reinserts all chunks for an article in one transaction, per section 10.3.
    Each chunk dict has keys: chunk_index, heading, content. Embeddings are added afterward
    by the embed_article job, which updates rows in place rather than reinserting."""
    async with conn.transaction():
        await conn.execute("delete from kb_chunks where workspace_id = $1 and article_id = $2", workspace_id, article_id)
        for chunk in chunks:
            await conn.execute(
                """
                insert into kb_chunks (workspace_id, article_id, chunk_index, heading, content)
                values ($1, $2, $3, $4, $5)
                """,
                workspace_id,
                article_id,
                chunk["chunk_index"],
                chunk.get("heading"),
                chunk["content"],
            )


async def list_chunks_without_embedding(conn: asyncpg.Connection, workspace_id: uuid.UUID, article_id: uuid.UUID) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, chunk_index, heading, content
          from kb_chunks
         where workspace_id = $1 and article_id = $2 and embedding is null
         order by chunk_index asc
        """,
        workspace_id,
        article_id,
    )


async def set_chunk_embedding(conn: asyncpg.Connection, chunk_id: uuid.UUID, embedding: list[float]) -> None:
    await conn.execute("update kb_chunks set embedding = $2 where id = $1", chunk_id, embedding)


async def vector_search(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, query_embedding: list[float], limit: int = 20
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select c.id as chunk_id, c.article_id, c.heading, c.content,
               row_number() over (order by c.embedding <=> $2) as rank
          from kb_chunks c
         where c.workspace_id = $1 and c.embedding is not null
         order by c.embedding <=> $2
         limit $3
        """,
        workspace_id,
        query_embedding,
        limit,
    )


async def lexical_search(
    conn: asyncpg.Connection, workspace_id: uuid.UUID, query: str, limit: int = 20
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select c.id as chunk_id, c.article_id, c.heading, c.content,
               row_number() over (order by ts_rank_cd(c.tsv, websearch_to_tsquery('english', $2)) desc) as rank
          from kb_chunks c
         where c.workspace_id = $1 and c.tsv @@ websearch_to_tsquery('english', $2)
         order by ts_rank_cd(c.tsv, websearch_to_tsquery('english', $2)) desc
         limit $3
        """,
        workspace_id,
        query,
        limit,
    )


async def get_articles_by_ids(conn: asyncpg.Connection, workspace_id: uuid.UUID, article_ids: list[uuid.UUID]) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        select id, workspace_id, category_id, title, slug, body_md, status, published_at, updated_at, created_at
          from kb_articles
         where workspace_id = $1 and id = any($2::uuid[])
        """,
        workspace_id,
        article_ids,
    )
