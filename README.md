# Intercom

A multi tenant customer communication platform: chat widget, email channel, unified inbox, knowledge base, AI issue summarisation, and custom domains for the knowledge base.

See [USER-GUIDE.md](USER-GUIDE.md) for a feature walkthrough. This README focuses on the two parts that carry the most design weight: the inbox/conversation workflow, and the AI layer.

## 1. Live URLs

| What | URL |
|---|---|
| Dashboard | `https://intercom-domains-1006131400305.asia-east1.run.app/inbox` (redirects to `/login`) |
| Demo page with the widget installed | `https://intercom-domains-1006131400305.asia-east1.run.app/demo` |
| Public knowledge base (seeded demo workspace) | `https://intercom-domains-1006131400305.asia-east1.run.app/kb/public/demo` |
| Health check | `https://intercom-domains-1006131400305.asia-east1.run.app/healthz` |
| Support email to test inbound | value of `sssid0708@gmail.com` |

## 2. Test credentials

Seeded demo workspace (`scripts/seed.py`), safe to re-run: workspace slug `demo`, admin `admin@demo.example` / `demopass123`. Fresh signup also works end to end, no seed data required.

## 3. Architecture, in one paragraph

Everything (API, WebSocket hub, job worker, email poller, summary debouncer) runs in one FastAPI process and one container, backed by Postgres 15 with pgvector for relational data, the job queue, and vector search. This is a deliberate modular-monolith choice for a one-instance deployment: it removes service discovery and inter-service calls at zero real cost, while the job queue (a Postgres table) and the `EventBus` interface are the seams that let a worker or the realtime hub split into their own process later without a rewrite.

## 4. The conversation workflow

This is the core of the product: one inbox, one data model, two channels feeding into it identically.

**Unified inbox.** `/inbox` lists every conversation regardless of channel, filterable by channel, status (open/snoozed/resolved), and assignee. Assign, snooze, and resolve are first-class actions, not afterthoughts; snoozed conversations reopen automatically via a scheduled job rather than a client-side timer.

**One send path for both channels.** A chat message and an inbound email both go through the exact same `send_message` function: a row-locked `UPDATE conversations SET last_seq = last_seq + 1 ... RETURNING last_seq` inside the insert transaction. That returned `seq`, not `created_at`, is what clients sort by, so client clock skew or out-of-order delivery can never reorder a thread. Because both channels share this path, ordering, idempotency, and AI-summary triggers behave identically no matter where a message came from.

**Idempotent, realtime delivery.** The widget tags every send with a `client_msg_id`; a unique index turns a retried send into a no-op, giving exactly-once effect over a WebSocket transport that is only at-least-once. On reconnect, the client replays via `sync` (`seq > since_seq`) straight from Postgres, the source of truth, not the socket, so a dropped connection never loses or duplicates a message.

**Email threading.** Inbound email is resolved to the right conversation through a four-step fallback chain (headers, an HMAC-verified reply-to token, subject matching, then a new conversation), specifically because real mail clients often strip the headers that naive threading relies on. An agent's reply and its `send_email` job are written in the same transaction (outbox pattern), so an SMTP outage delays a reply instead of losing it.

## 5. The AI layer

The other core piece: AI never sits in the critical path of a page render, and every response is structured, not prose.

**Incremental conversation summaries.** Instead of resending the whole transcript to the LLM every time an agent opens a conversation (cost scales with conversation length), a stored `covered_through_seq` watermark means only messages since the last summary are sent on each refresh (cost scales with new messages only). The summary is a structured Pydantic model (what the customer wants, what's been tried, status, open questions, next action, sentiment), not a blob of text, and it updates live over the same WebSocket connection the inbox already uses.

**Never blocks, never fails silently.** Every LLM call goes through one client with a fixed ladder: budget check, primary model, one schema-repair retry, one fallback-model attempt on timeout/5xx, and a circuit breaker after 5 consecutive failures. Every call is logged (tokens, cost, latency, status) to power `/settings/ai`, whether it succeeds or not. If the LLM is unavailable entirely, the UI serves the last stored summary with a staleness flag, or an extractive summary with no LLM call at all if none exists yet, so the inbox is never blocked on AI availability.

**Hybrid KB search.** Powers both the public KB search page and the widget's in-chat auto-suggest from one function: pgvector cosine similarity and Postgres full-text search run independently and are fused with reciprocal rank fusion. No cross-encoder reranker, deliberately, since the latency cost isn't worth it for a 3-item dropdown that fires while someone is mid-keystroke.

**Untrusted input.** Conversation text is wrapped in explicit delimiters in every prompt and stated to be data, never instructions, as the mitigation against a customer message trying to redirect the summariser.

## 6. Deployment notes

- **Custom domain mapping is a manual gcloud step today**, not yet automated:
  ```bash
  gcloud beta run domain-mappings create \
    --service=intercom-domains \
    --domain=help-demo.readiq.app \
    --region=asia-east1
  ```
  In-app, a domain only gets DNS-verified (CNAME + TXT, checked by a background job); the actual Cloud Run mapping is created by hand afterward. The natural automation path is having that same verification job call the Cloud Run Admin API (`domainmappings.create`) directly instead of a human running the command.
- **Region: `asia-east1`, not `asia-south1`.** The service was first deployed to `asia-south1` (Mumbai), which does not support Cloud Run domain mappings. It was redeployed to `asia-east1` (Taiwan), which does, so custom domain verification could be exercised end to end.
- **CI/CD.** GitHub Actions (`.github/workflows/ci.yml`) runs backend tests against a throwaway Postgres+pgvector container, a frontend typecheck/build, and a Docker image build, on every push and pull request. There is no CD step yet; deploys to Cloud Run are triggered manually.
- **TLS** for both the default `run.app` URL and any mapped custom domain is terminated by Cloud Run itself; no certificate handling is implemented in the app since there's nothing to plug it into on this host.

## 7. Tenant isolation and security, briefly

Every repository function takes `workspace_id` explicitly and includes it in the WHERE clause, even on primary-key lookups; `tests/test_isolation.py` asserts cross-tenant reads return nothing. Argon2id password hashing, HttpOnly/SameSite session cookies, scoped short-lived visitor JWTs that can't reach dashboard routes, sanitised KB markdown, and per-route rate limits are all in place. CSP on the widget iframe and an explicit Origin header check are deferred. Full checklist and rationale for every deferral lives in git history / prior README revisions; kept out of this version to keep the focus on workflow and AI.

## 8. Tradeoffs, deliberately cut

- Custom domain TLS and Cloud Run domain-mapping creation are manual/stubbed, only DNS verification is automated
- Rate limiting and the realtime hub are in-process, not Redis-backed, so neither coordinates across more than one instance
- No cross-encoder reranker on KB search, no CSP on the widget iframe, no password reset or signup email verification
- Email attachments recorded as filenames only, never stored
- No Redis, Celery, Alembic, Kubernetes, or Terraform anywhere in the stack, by design

## 9. Local setup

```bash
cp .env.example .env
# edit .env: set SESSION_SECRET, and optionally GEMINI_API_KEY, SUPPORT_EMAIL/SMTP_*/IMAP_HOST for email
docker compose up --build
docker compose exec app python -m scripts.seed
```

Then open `http://localhost:8000/inbox` and sign up, or log in with the seeded credentials from section 2.
