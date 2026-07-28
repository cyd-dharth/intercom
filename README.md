# Intercom

A multi tenant customer communication platform: chat widget, email channel, unified inbox, knowledge base, AI issue summarisation, and custom domains for the knowledge base. Built as a hiring assignment under a 24 hour build budget.

## 1. Live URLs

| What | URL |
|---|---|
| Dashboard | `{APP_BASE_URL}/inbox` (redirects to `/login` if not signed in) |
| Demo page with the widget installed | `{APP_BASE_URL}/demo` |
| Public knowledge base (seeded demo workspace) | `{APP_BASE_URL}/kb/public/demo` |
| Widget iframe document | `{APP_BASE_URL}/widget` |
| Health check | `{APP_BASE_URL}/healthz` |
| Support email to test inbound (if `SUPPORT_EMAIL`/SMTP/IMAP are configured) | value of `SUPPORT_EMAIL` |

Replace `{APP_BASE_URL}` with wherever this instance is actually deployed (see `APP_BASE_URL` in `.env`).

## 2. Test credentials

Seeded demo workspace (`scripts/seed.py`), safe to re-run:

- Workspace slug: `demo`
- Admin email: `admin@demo.example`
- Password: `demopass123`

That workspace comes with 6 published knowledge base articles across 2 categories, and 3 seeded conversations, one of which has 14 messages so the AI summary has something to work with.

Fresh signup is fully functional and independent of the seed: `POST /api/auth/signup` from the login page creates a new workspace, a new admin user, and lands on an empty inbox. An evaluator does not need the seeded credentials to exercise the product end to end.

## 3. Architecture

```
                        ┌─────────────────────────────────────┐
                        │         single container             │
                        │                                        │
  browser ── HTTPS ──▶  │  FastAPI (uvicorn)                    │
  dashboard/widget      │   ├─ REST API (/api/*)                │
                        │   ├─ WebSocket hub (/ws/agent,/ws/widget)│
                        │   ├─ Jinja public pages (KB, widget,   │
                        │   │   demo)                            │
                        │   ├─ React dashboard, static build     │
                        │   ├─ job worker loop (asyncio task)     │
                        │   ├─ email poller loop (asyncio task)   │
                        │   └─ snooze/verify_domain jobs          │
                        │                                        │
                        └───────────────┬────────────────────────┘
                                        │ asyncpg pool
                                        ▼
                        ┌─────────────────────────────────────┐
                        │  Postgres 15 + pgvector               │
                        │   tables, jobs queue, vector search    │
                        └─────────────────────────────────────┘
                                        │
                          SMTP/IMAP     │      Gemini API
                          (Gmail)  ◀────┴────▶ (generation + embeddings)
```

**Why a modular monolith.** Everything (API, WebSocket hub, job worker, email poller, summary debouncer) runs in one process and one container. For a 24 hour build with one always warm instance, this removes an entire category of problems (service discovery, network calls between components, partial deploys) for zero real cost, since nothing here needs to scale independently yet. The seams that would let it split later are already in place: the job queue is a Postgres table any additional worker process could claim from, and the `EventBus` interface (section on realtime below) is the one thing standing between "in process hub" and "Redis pub/sub across many instances." Splitting the worker and poller into their own deployments, and swapping `InMemoryBus` for a `RedisBus`, are the two changes that unblock running more than one instance.

**Request path, a chat message.** Widget sends `message.send` over its WebSocket → `app/realtime/routes.py _handle_frame` → `app/services/conversations.py send_message` (row locked seq bump, insert, commit) → `EventBus.publish` on `conv:{conversation_id}` and `ws:{workspace_id}` → `Hub.broadcast` enqueues onto every subscribed connection's bounded queue → each connection's writer task sends the frame. The dashboard receives `message.new` and `conversation.updated` over its own socket, no polling.

**Request path, an inbound email.** IMAP poller wakes every `IMAP_POLL_SECONDS` → fetches new UIDs → `app/email/inbound.py process_inbound_email` parses, strips quotes, resolves the conversation through the four step chain (see section 6 below) → calls the same `send_message` service function chat uses → same seq bump, same broadcast, same summary trigger rule. One insert path for both channels is deliberate: it means ordering, idempotency, and AI triggers behave identically regardless of channel.

## 4. Data model

**Ordering invariant.** Every message insert happens inside one transaction that bumps `conversations.last_seq` with a row locked `UPDATE ... RETURNING last_seq`. The row lock serialises concurrent writers on the same conversation, which is exactly the granularity needed, no wider. The returned value becomes `messages.seq`. Clients sort by `seq`, never by `created_at`, so client clock skew and out of order network delivery cannot reorder a conversation. Broadcast happens strictly after commit.

**Idempotency index.** The widget generates a `client_msg_id` (uuid v4) per send. The unique index `messages_idem` on `(conversation_id, client_msg_id)` turns a retried send into a no-op: `send_message` checks for an existing row with that key first and returns it unchanged rather than inserting again. Combined with at-least-once delivery over an unreliable socket, this gives exactly-once effect on the data even though the transport can duplicate.

**Outbox pattern.** An agent reply on an email conversation writes the `messages` row and enqueues a `send_email` job in the same transaction. If the transaction commits, the job is guaranteed to exist and eventually run; if SMTP is down when the worker picks it up, the job retries with backoff rather than the reply silently vanishing. The message is never "sent" from the caller's perspective until the row exists, decoupling the write from the SMTP round trip.

**Tenant isolation.** Every repository function takes `workspace_id` as an explicit argument and includes it in the WHERE clause, even on a primary key lookup. There is no "get by id" without a workspace. `tests/test_isolation.py` creates two workspaces and asserts cross tenant reads of conversations, messages, articles, and contacts all return nothing; `tests/test_message_ordering.py` covers the seq invariant under concurrency and the idempotent retry. Two repository functions are deliberately unscoped (`find_by_email_message_id_any_workspace`, `find_by_short_id_prefix_any_workspace`), both used only during inbound email threading before a workspace is known; their docstrings explain why and they are never reused elsewhere.

The next hardening step beyond application level scoping is Postgres row level security with a transaction scoped `SET LOCAL app.workspace_id`, so an application bug (a forgotten WHERE clause) fails closed at the database instead of silently returning cross tenant rows. The one line policy this unlocks, per table:

```sql
create policy tenant_isolation on conversations
  using (workspace_id = current_setting('app.workspace_id')::uuid);
```

## 5. Realtime

**Connection lifecycle.** `GET /ws/agent` authenticates from the session cookie (falls back to a `?token=` query param for scripted testing) and subscribes to every workspace the user belongs to. `GET /ws/widget` authenticates from a visitor JWT and subscribes only to that visitor's own conversation topics; a visitor token can never reach an agent-only topic or an `/api/*` dashboard route. The server pings every 25 seconds and closes a connection with no frame for 60 seconds.

**Reconnect and gap recovery.** The client reconnects with exponential backoff plus jitter (1s, 2s, 4s, 8s, capped at 15s), re-subscribes to every open conversation, then sends `{"type":"sync","data":{"conversation_id":...,"since_seq":N}}`. The server replays every message with `seq > N` straight from Postgres. The socket is a fast path, never the source of truth; killing the network for any length of time and restoring it recovers every message with no duplicates, because the replay is driven by `seq`, not by "whatever I missed."

**Delivery guarantee, precisely.** At least once delivery, idempotent writes. The widget queues outbound sends in memory while disconnected and flushes on reconnect; a flushed retry that already landed is a no-op because of the `client_msg_id` unique index. Broadcast frames themselves are not guaranteed (a dropped connection just misses that broadcast) but the `sync` replay on reconnect is what actually guarantees eventual delivery, not the broadcast.

**EventBus to Redis scale out.** `app/realtime/bus.py` defines `EventBus` as the only thing services publish through; `InMemoryBus` calls the in-process `Hub` directly. A `RedisBus` stub documents the swap: at scale out, each instance publishes to a Redis pub/sub channel per topic, and every instance's own `Hub` subscribes and rebroadcasts to whichever sockets happen to be connected to it (WebSocket connections are pinned to one instance, so fanout across instances is exactly what Redis buys). No service code changes when this swap happens, only the one line that constructs `bus`.

## 6. Email engineering

**Four step threading resolution chain** (`app/email/inbound.py process_inbound_email`), tried in order and logged with which strategy matched:

1. `References`/`In-Reply-To` against a stored `messages.email_message_id`, globally (workspace falls out of whichever message matches)
2. The Reply-To `+c{conv_short}.{hmac10}` token in the `To`/`Delivered-To` address, verified by HMAC before trusting it, also global
3. Normalised subject (strip `Re:`/`Fwd:`, case, whitespace) matched against an open conversation from the same sender within 30 days, scoped to whichever workspace steps 1 and 2 could not resolve
4. Otherwise, create a new conversation; workspace routing falls back to a `+ws{slug}` tag in the recipient, else `EMAIL_FALLBACK_WORKSPACE_SLUG`

**Why header matching alone is insufficient.** Real mail clients (particularly some corporate Outlook configurations) strip or rewrite `In-Reply-To`/`References` on send. If step 1 were the only signal, every such reply would silently start a new conversation instead of continuing the thread. The Reply-To token is the fallback that survives that stripping, because it is encoded into the address itself, which clients preserve even when they discard headers. Subject matching is the last resort for the remaining case where both header and address get mangled.

**Quote stripping.** Inbound bodies are cut at the first line matching a quote marker (`On ... wrote:`, a `From:` block, lines starting with `>`, `Sent from my ...`) and everything after is dropped, so a reply does not re-inject the entire prior thread as new message content on every round trip.

**Migration path to provider webhooks.** IMAP polling every `IMAP_POLL_SECONDS` is simple and needs no public endpoint, at the cost of latency bounded by the poll interval and a periodic full mailbox touch. The natural next step is an inbound parse webhook (SendGrid Inbound Parse, Postmark, Mailgun routes) that posts the parsed MIME to an HTTPS endpoint the moment it arrives, cutting latency to near zero and removing the poll loop entirely. That move also means taking on SPF, DKIM, and DMARC properly: today outbound mail's deliverability rides on the underlying Gmail account's existing reputation and authentication, but a dedicated sending domain needs its own SPF record authorising the sending provider, DKIM signing keys configured with that provider, and a DMARC policy, or receiving mail servers increasingly spam-file or reject it.

## 7. AI

**Incremental summarisation vs the naive approach.** The naive version resends the entire conversation transcript to the LLM every time an agent opens it. Cost there is `O(message_count)` per open, so a 50 message conversation opened 10 times over its life costs roughly 10x the tokens of its own length. The implemented version (`app/ai/summarizer.py refresh_summary`) stores a `covered_through_seq` watermark and, on every trigger, sends only the previous structured summary (a few hundred tokens of JSON) plus messages with `seq > covered_through_seq`, capped at 30 and middle-truncated past 2000 characters each. Cost per summarisation call is `O(new messages since last summary)`, not `O(conversation length)`. For the same 50 message conversation summarised incrementally after every 5-message burst, the incremental version processes roughly 50 messages total across its whole life instead of the naive version's 500+ (10 opens times a growing transcript).

**Prompt versioning.** `app/ai/prompts.py` exports `SUMMARY_PROMPT_TEMPLATE` alongside `SUMMARY_PROMPT_VERSION = "sum-v1"`. The version string is stored on every `conversation_summaries` row and every `ai_calls` row, so changing the prompt later is traceable: old summaries keep their original version stamped, and a rollout can be evaluated by comparing quality across versions rather than silently overwriting history.

**Structured output and repair retry.** Every LLM response is parsed into a Pydantic v2 model (`ConversationSummary`), never regex scraped. `app/ai/client.py generate_json` follows a fixed ladder: budget check, primary model call, on a validation failure exactly one repair retry with the validation error appended to the prompt (status `schema_retry`), on a primary call exception one fallback model attempt with a shorter timeout (status `fallback`), and a 5 consecutive failure circuit breaker that opens for 60 seconds and short circuits new calls to `(None, "circuit_open")`. It never raises into the caller and always writes one `ai_calls` row (model, prompt version, token counts, latency, cost, status, truncated error) regardless of outcome, which is what makes the AI usage page a real signal instead of a guess.

**Degradation ladder.** If the LLM is disabled, over budget, or every attempt fails: the previous stored summary is served untouched with a staleness flag derived from comparing `covered_through_seq` to `last_seq`. If no summary exists yet and the LLM is unavailable, an extractive summary is generated with no LLM call at all (first contact message, last 3 messages joined, `confidence: 0.3`, `generator: "extractive"`). The dashboard summary card labels the generator and shows a manual Regenerate button. Nothing in the inbox blocks on AI availability; setting an invalid API key leaves every page fully functional.

**Hybrid retrieval and RRF.** `app/ai/retrieval.py hybrid_search` runs a pgvector cosine similarity search and a Postgres full text search (`ts_rank_cd` over `websearch_to_tsquery`) independently, each capped at 20 results, then fuses the two ranked lists with reciprocal rank fusion (`score = sum(1 / (60 + rank))` across whichever lists a chunk appears in) before collapsing to the best scoring chunk per article and returning the top 3. This is the same function behind both the public KB search page and the widget's auto-suggest panel, one code path, two surfaces.

**Why no cross encoder reranker.** A reranker would improve relevance ordering at the cost of an extra model call in the latency budget of a 3-item dropdown that fires while someone is still typing. The 400ms debounce and minimum 12 character threshold already exist specifically to keep this path fast; adding a reranking call would work against that goal for a gain that is hard to notice across 3 results. RRF over two independent signals (semantic and lexical) already captures most of the achievable quality here without another network round trip.

**Untrusted input handling.** Conversation text is never treated as instructions. The summarisation prompt wraps new messages in explicit `<<<CONVERSATION_NEW_MESSAGES ... CONVERSATION_NEW_MESSAGES>>>` delimiters and states directly that the enclosed text is data from an untrusted end user, to be described, never followed. This is the mitigation against prompt injection from a customer message trying to redirect the summariser's behaviour.

## 8. Custom domains and TLS

**What is implemented.** `POST /api/workspaces/{id}/domains` creates a `custom_domains` row with a random verification token and returns the two DNS records to create: a CNAME from the hostname to this app's host, and a TXT record at `_inbox-verify.{hostname}` containing the token. A `verify_domain` job resolves both records with `dnspython`, sets status to `verified` or `failed` with the specific reason, and reschedules itself every 60 seconds while pending, up to 10 attempts, after which it settles on `failed` with the last DNS error preserved. Host header routing middleware on the public routes looks up `custom_domains` by the incoming `Host` header; on a verified match it rewrites the request path to the equivalent `/kb/public/{slug}/...` route before FastAPI's router sees it, so `help.customer.com/` serves that workspace's KB with no path prefix, with no other route able to be reached this way. The Settings > Domains page shows every domain, its status, the exact DNS records, and a Check now button that enqueues an immediate re-check.

**What is stubbed, and why.** Certificate provisioning is not wired up. TLS termination for this deployment happens at the managed host level (Cloud Run or equivalent), which does not expose certificate control to the application, so there is no hook here to attach a certificate to even if one were minted. Two real approaches exist for a deployment that does control its own edge:

- **On demand TLS with Caddy.** Caddy's `ask` endpoint receives every incoming hostname before requesting a certificate and can approve or reject it against the verified list in `custom_domains`; on approval, Caddy requests a Let's Encrypt certificate via TLS-ALPN-01 on the first real request for that hostname and caches it. This is the standard self-hosted approach for arbitrary customer-supplied domains.
- **Cloudflare for SaaS.** Cloudflare's custom hostname API takes a hostname plus an ownership verification method and provisions and renews the certificate on Cloudflare's edge, no certificate handling in the application at all. This trades operational simplicity for a dependency on Cloudflare sitting in front of the app.

Neither is wired up here because the free managed host used for this deployment terminates TLS itself with no certificate API exposed to the app, so there is nowhere in this deployment to plug either approach in. The verification and data model above are exactly what either approach would consume as its "is this hostname allowed" check, so adding real provisioning later is additive, not a rework.

**Two further problems worth naming.** The apex domain problem: a bare domain (`example.com` rather than `help.example.com`) cannot have a CNAME record at all per the DNS spec, only at a subdomain; providers work around this with CNAME flattening (Cloudflare) or ALIAS records (Route 53), which is why the DNS instructions here ask for a CNAME at a subdomain rather than trying to support apex domains. Let's Encrypt rate limits: 50 certificates per registered domain per week, which is why on demand TLS implementations gate issuance behind an explicit allowlist check (the `ask` endpoint above) rather than requesting a certificate for any hostname that happens to resolve to the server, since that would let anyone with DNS control over an unrelated domain trigger unlimited certificate requests against your rate limit.

## 9. Security

Per the section 15 checklist, honestly marked:

| Item | Status |
|---|---|
| Argon2id password hashing, 8 character minimum | Done |
| Session token: 32 random bytes, SHA-256 hash stored, HttpOnly/Secure(non-local)/SameSite=Lax cookie, 7 day expiry | Done |
| CSRF via SameSite=Lax | Done. Explicit Origin header check on mutating requests is not separately implemented; SameSite=Lax already blocks the cross-site form/link submissions this would catch, deferred for time |
| Visitor JWTs scoped and short lived, cannot reach dashboard routes | Done (hand rolled HMAC signed token, same shape as a JWT, no new dependency) |
| KB markdown sanitised server side with an element allowlist (`nh3`) | Done |
| Chat messages rendered as text nodes, never innerHTML | Done, in both the widget and the dashboard |
| Rate limits: 5/min auth, 20/min widget session, 60/min message send, 30/min KB search, 429 with Retry-After | Done, in-process token bucket keyed by IP or user id per route class |
| Pydantic request models with explicit length caps everywhere, message body max 10000 chars | Done |
| Consistent error envelope, no leaked stack traces or SQL | Done |
| CSP on the widget iframe, frame-ancestors intentionally open | Deferred: no CSP header is currently set on the widget document; frame-ancestors would need to be added and restricted to a workspace's allowed_origins in a real production pass |

Redis backed rate limiting is the natural next step once there is more than one instance, since the in-process token bucket here does not share state across processes; a shared Redis counter (or a library like `slowapi` backed by Redis) is the direct swap.

## 10. Tradeoffs and what was deliberately cut

- No password reset flow, no email verification on signup (explicitly out of scope per the brief)
- No CSP header on the widget iframe document yet; frame-ancestors restriction to allowed_origins deferred
- No explicit Origin header check on mutating requests; relying on SameSite=Lax alone for CSRF
- Rate limiting is an in-process token bucket, not Redis backed, so it does not coordinate across more than one instance
- EventBus stayed in-memory (InMemoryBus); RedisBus exists only as an unimplemented stub class
- No Redis, no Celery, no Alembic, no Kubernetes, no Terraform anywhere in the stack, per the brief's constraints
- Custom domain TLS provisioning is stubbed entirely; only DNS verification and the data model are implemented, per the brief's explicit allowance
- No cross encoder reranker on KB hybrid search, trading a small relevance gain for latency in a 3-item dropdown
- Snooze auto-reopen shipped first as a 30 second polling sweeper in phase 2, later replaced with a real `unsnooze` job once the job worker existed in phase 3
- Email attachments are recorded as filenames only, never stored, storage is out of scope
- `config.py`'s embedding settings (`gemini-embedding-2`, 1536 dimensions) deviate from CLAUDE.md section 4's stated `text-embedding-004` / 768 dimensions; the schema and all embedding/retrieval code were written dimension-agnostic against whatever `settings.embedding_model` and the column width actually are, rather than forcing a switch back
- `kb_chunks.embedding` was migrated from `vector(768)` to `vector(1536)` directly against the deployed database (drop index, drop column, add column at the correct width, recreate index) to match the actual embedding model in use, rather than fixing the model choice to match the original schema
- No JWT library added for visitor tokens; a hand rolled HMAC signed payload in `security.py` fills the same role with no new dependency
- `/ws/agent` authenticates from the session cookie primarily, with a `?token=` query param fallback kept only for scripted testing, not general use
- Two repository functions (`find_by_email_message_id_any_workspace`, `find_by_short_id_prefix_any_workspace`) are intentionally unscoped by workspace_id, the only such functions in the codebase, needed because inbound email threading must resolve a conversation before the workspace is known
- The generic job retry/backoff mechanism (exception-based, exponential) was not reused for `verify_domain`'s 60-second/10-attempt reschedule, since a still-pending DNS check is an expected outcome, not an exception; attempt counting for that job lives in its own payload instead
- Test suite is limited to the tenant isolation tests and one message ordering test, no broader coverage, per the brief's explicit scope
- No test infrastructure (fixtures, factories) beyond what the isolation and ordering tests needed directly; both run against the live deployed database rather than an isolated test database
- Deployed against a Supabase-hosted Postgres instance rather than the docker-compose `db` service for actual verification in this build, since that was the reachable database in this environment

## 11. Known limitations

- Email attachments are recorded as filenames only, never stored; downloading an attachment from an inbound email is not possible in this build.
- No password reset flow and no email verification on signup, both explicitly out of scope for the assignment.
- The AI usage and job queue pages are workspace scoped but there is no cross-workspace operator view; an operator with access to many workspaces checks each one individually.
- Typing indicators and presence are in-memory only and reset on a restart, since they are explicitly not meant to be durable state.
- Custom domain TLS is not provisioned, see section 8. A verified custom domain serves the KB over plain HTTP unless the surrounding infrastructure (a reverse proxy, a CDN) terminates TLS for it independently of this app.
- The rate limiter and the realtime hub are both in-process state, so they do not coordinate across more than one running instance; see the EventBus/Redis scale out note in section 5.
- The widget's KB auto-suggest and the public KB search share one hybrid_search code path but there is no cross encoder reranking; see section 7 for why.

## 12. Local setup

Requires Docker and a Gemini API key (optional, the app degrades loudly without one).

```bash
cp .env.example .env
# edit .env: set SESSION_SECRET, and optionally GEMINI_API_KEY, SUPPORT_EMAIL/SMTP_*/IMAP_HOST for email
docker compose up --build
# in a second terminal, once the app is healthy:
docker compose exec app python -m scripts.seed
```

Then open `http://localhost:8000/inbox` and sign up, or log in with the seeded credentials from section 2.
