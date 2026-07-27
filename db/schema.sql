create extension if not exists pgcrypto;
create extension if not exists vector;

/* ============ tenancy and identity ============ */

create table if not exists workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  public_key text not null unique,
  allowed_origins text[] not null default '{}',
  ai_daily_budget_cents int not null default 200,
  created_at timestamptz not null default now()
);

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  password_hash text not null,
  name text not null,
  created_at timestamptz not null default now()
);
create unique index if not exists users_email_key on users (lower(email));

create table if not exists workspace_members (
  workspace_id uuid not null references workspaces(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,
  role text not null check (role in ('admin','agent')),
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create table if not exists invites (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  email text not null,
  role text not null check (role in ('admin','agent')),
  token_hash text not null unique,
  invited_by uuid references users(id) on delete set null,
  accepted_at timestamptz,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id) on delete cascade,
  token_hash text not null unique,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

/* ============ contacts and conversations ============ */

create table if not exists contacts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  visitor_id text,
  email text,
  name text,
  last_seen_at timestamptz,
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create unique index if not exists contacts_ws_visitor on contacts (workspace_id, visitor_id) where visitor_id is not null;
create unique index if not exists contacts_ws_email on contacts (workspace_id, lower(email)) where email is not null;

create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  contact_id uuid not null references contacts(id) on delete cascade,
  channel text not null check (channel in ('chat','email')),
  status text not null default 'open' check (status in ('open','snoozed','resolved')),
  subject text,
  assignee_id uuid references users(id) on delete set null,
  last_seq bigint not null default 0,
  message_count int not null default 0,
  last_message_at timestamptz,
  snoozed_until timestamptz,
  first_response_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists conversations_inbox on conversations (workspace_id, status, last_message_at desc);
create index if not exists conversations_assignee on conversations (workspace_id, assignee_id);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  seq bigint not null,
  sender_type text not null check (sender_type in ('contact','agent','system')),
  sender_user_id uuid references users(id) on delete set null,
  body text not null,
  client_msg_id text,
  email_message_id text,
  email_in_reply_to text,
  created_at timestamptz not null default now()
);
create unique index if not exists messages_conv_seq on messages (conversation_id, seq);
create unique index if not exists messages_idem on messages (conversation_id, client_msg_id) where client_msg_id is not null;
create unique index if not exists messages_email_mid on messages (workspace_id, email_message_id) where email_message_id is not null;
create index if not exists messages_conv_order on messages (conversation_id, seq desc);

create table if not exists read_state (
  conversation_id uuid not null references conversations(id) on delete cascade,
  participant text not null,
  last_read_seq bigint not null default 0,
  updated_at timestamptz not null default now(),
  primary key (conversation_id, participant)
);

/* ============ knowledge base ============ */

create table if not exists kb_categories (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  name text not null,
  slug text not null,
  position int not null default 0
);
create unique index if not exists kb_categories_slug on kb_categories (workspace_id, slug);

create table if not exists kb_articles (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  category_id uuid references kb_categories(id) on delete set null,
  title text not null,
  slug text not null,
  body_md text not null default '',
  status text not null default 'draft' check (status in ('draft','published')),
  published_at timestamptz,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create unique index if not exists kb_articles_slug on kb_articles (workspace_id, slug);

create table if not exists kb_chunks (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  article_id uuid not null references kb_articles(id) on delete cascade,
  chunk_index int not null,
  heading text,
  content text not null,
  embedding vector(1536),
  tsv tsvector generated always as (to_tsvector('english', content)) stored
);
create index if not exists kb_chunks_tsv on kb_chunks using gin (tsv);
create index if not exists kb_chunks_vec on kb_chunks using hnsw (embedding vector_cosine_ops);
create index if not exists kb_chunks_article on kb_chunks (article_id);

/* ============ ai ============ */

create table if not exists conversation_summaries (
  conversation_id uuid primary key references conversations(id) on delete cascade,
  workspace_id uuid not null references workspaces(id) on delete cascade,
  summary jsonb not null,
  covered_through_seq bigint not null default 0,
  model text not null,
  prompt_version text not null,
  generator text not null default 'llm',
  updated_at timestamptz not null default now()
);

create table if not exists ai_calls (
  id bigserial primary key,
  workspace_id uuid,
  kind text not null,
  model text not null,
  prompt_version text,
  input_tokens int,
  output_tokens int,
  cost_micros bigint not null default 0,
  latency_ms int,
  status text not null,
  error text,
  created_at timestamptz not null default now()
);
create index if not exists ai_calls_ws_time on ai_calls (workspace_id, created_at desc);

/* ============ jobs ============ */

create table if not exists jobs (
  id bigserial primary key,
  workspace_id uuid,
  kind text not null,
  payload jsonb not null default '{}',
  dedupe_key text,
  run_after timestamptz not null default now(),
  attempts int not null default 0,
  max_attempts int not null default 5,
  status text not null default 'pending' check (status in ('pending','running','done','dead')),
  last_error text,
  locked_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists jobs_dedupe on jobs (dedupe_key) where status in ('pending','running');
create index if not exists jobs_claim on jobs (run_after) where status = 'pending';

/* ============ custom domains ============ */

create table if not exists custom_domains (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  hostname text not null unique,
  verification_token text not null,
  status text not null default 'pending' check (status in ('pending','verified','failed')),
  last_checked_at timestamptz,
  last_error text,
  created_at timestamptz not null default now()
);

/* ============ email sync cursor ============ */

create table if not exists email_sync_state (
  id int primary key default 1,
  last_uid bigint not null default 0,
  updated_at timestamptz not null default now()
);
insert into email_sync_state (id) values (1) on conflict do nothing;
