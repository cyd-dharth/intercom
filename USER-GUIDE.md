# Intercom Platform - User Guide

## What this app is

A support inbox platform where customers reach your team through a chat widget and email, and your team works every conversation from one unified inbox, with AI doing the busywork of keeping track of what's going on in each thread.

---

# 1. Log in

Open:

```
https://intercom-domains-1006131400305.asia-east1.run.app/login
```

Demo credentials:

```
Email: admin@demo.example
Password: demopass123
```

Or click "Sign up" to create your own workspace from scratch. After login you land on the inbox, pre-populated with 3 sample conversations and 6 help articles.

---

# 2. The unified inbox (core workflow)

Open `/inbox`. This is where the day-to-day work happens: every conversation from chat and email lands in one list, so your team never has to check two places.

- **Filter** by channel (chat/email), status (open/snoozed/resolved), or assignee, to work through exactly the slice you own
- **Assign** a conversation to a teammate, ownership is explicit, not implicit
- **Snooze** a conversation when you're waiting on something, it reopens automatically when the snooze period ends
- **Resolve** once the conversation is done
- Click into a conversation to see the full thread and reply from one composer, regardless of whether the customer wrote in over chat or email. Threading, ordering, and history all work identically across both.

This is the same inbox for both channels by design: an agent should never need to think about which system a message came from.

---

# 3. Chat widget

1. Open the demo page in a separate/incognito browser window: `.../demo`
2. Click the chat launcher, type a message, and send it.
3. Switch to the dashboard inbox: the new conversation appears instantly, no refresh.
4. Reply from the dashboard, it appears in the widget instantly. Typing indicators show live in both directions.

Message history persists per visitor, so returning visitors see their past conversation. Install on any site with one script tag:

```html
<script src="https://YOUR_HOST/widget.js" data-key="WORKSPACE_PUBLIC_KEY" async></script>
```

---

# 4. Email channel

Once `SUPPORT_EMAIL`/`SMTP_*`/`IMAP_*` are configured, email works into the same inbox as chat.

1. Send an email to the support address (e.g. `sssid0708@gmail.com`).
2. Wait up to 20 seconds, refresh the inbox. It appears as a new conversation (filter `Channel: email`).
3. Reply from the dashboard. It's sent as a correctly threaded reply.
4. Further replies from either side stay in the same conversation, matched automatically even if the customer's reply strips or mangles email headers.

---

# 5. AI conversation summaries (core AI feature)

This is the main AI feature in the product. Every conversation with 6+ messages gets a structured, LLM-generated summary shown in a card next to the thread:

- **What the customer wants**, what's been tried so far, current status, open questions, and a suggested next action, not just a wall of prose
- **Updates incrementally** as the conversation grows: new messages are summarised and merged into the existing summary rather than reprocessing the whole thread from scratch every time, so it stays current without repeatedly re-reading everything that was already said
- **Never blocks the inbox.** If the AI is temporarily unavailable, a basic fallback summary is shown instead of a spinner or an error, the inbox always works
- A **Regenerate** button forces a fresh summary on demand
- `/settings/ai` shows today's AI usage: number of calls, tokens, and cost, so AI spend is visible, not a black box

Watch it work: open the 14-message seeded conversation in the demo workspace to see a fully formed summary, then send a few more messages and see it update live within about 30 seconds.

---

# 6. Knowledge base and AI search

Open `/kb` to write and publish Markdown articles into categories.

- Public pages at `/kb/public/<workspace-slug>` include a working search box
- Inside the chat widget, typing a question (12+ characters) auto-suggests relevant articles as the customer types, before they even ask a human

Search itself is AI-assisted: it combines keyword matching with semantic (embedding-based) search, so it can surface the right article even when the customer's wording doesn't match the article's wording.

---

# 7. Custom domains

Serve your knowledge base from your own domain (e.g. `help.yourcompany.com`).

1. Go to `/settings/domains`, add your hostname, and create the CNAME and TXT records it gives you.
2. Click "Check now" or wait for the automatic recheck. Status moves from Pending to Verified.
3. Once verified, your domain serves the KB directly, no path prefix.

---

# Quick Reference

| Action | Location |
|---|---|
| Login as admin | `/login` |
| Admin email / password | `admin@demo.example` / `demopass123` |
| Unified inbox | `/inbox` |
| Customer chat widget | `/demo` (separate/incognito browser) |
| Knowledge base editor | `/kb` |
| Public knowledge base | `/kb/public/demo` |
| Test email | Send to `sssid0708@gmail.com` |
| Invite teammate | `/settings/team` |
| Custom domains | `/settings/domains` |
| AI usage | `/settings/ai` |

---

# Additional things to try

- Assign, snooze, and resolve a conversation, then filter the inbox to confirm the state
- Send several new messages into an existing conversation and watch the AI summary update live
- Publish a KB article, then find it via search on both the public page and inside the widget's auto-suggest
