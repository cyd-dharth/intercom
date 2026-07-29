# Intercom Platform - User Guide

## What this app is

A support inbox platform (similar to Intercom) where customers can reach your team through:

- A chat widget on your website
- Email support channel

All conversations are collected into a single inbox dashboard managed by your team.

Additional features:
- Knowledge base management
- AI-generated conversation summaries

---

# 1. Log in

Open:

```
https://intercom-1006131400305.asia-south1.run.app/login
```

Use the demo credentials:

```
Email: admin@demo.example
Password: demopass123
```

After login:
- You will be redirected to the inbox dashboard.
- The inbox is already populated with:
  - 3 sample conversations
  - 6 help articles

---

# 2. Start a conversation (Customer Chat Widget)

To test the customer chat flow:

1. Open the demo page in a different browser or incognito window:

```
http://localhost:8000/demo
```

2. You will see a demo product page with a chat launcher button.

3. Click the chat button, type a message, and send it.

4. Switch back to the admin dashboard:

```
http://localhost:8000/inbox
```

5. The new conversation will appear in the inbox in real time.

6. Reply from the dashboard.

7. The reply will instantly appear in the customer widget.

## Chat flow

```
Customer Widget
        |
        v
    Inbox Dashboard
        |
        v
     Team Reply
        |
        v
Customer Widget
```

---

# 3. Test the Email Channel

Email support works only when email settings are configured in `.env`.

Required configuration:

```
SUPPORT_EMAIL
SMTP_*
IMAP_*
```

Check configured support email:

```bash
grep -E "^SUPPORT_EMAIL=" d:/Projects/Intercom/.env
```

Example:

```
SUPPORT_EMAIL=sssid0708@gmail.com
```

## Test email workflow

1. From any email account, send a message to:

```
sssid0708@gmail.com
```

2. Wait up to 20 seconds.

3. Refresh the dashboard inbox.

4. The email appears as a new conversation.

5. Filter by:

```
Channel: email
```

6. Reply from the dashboard.

7. The reply is sent back to the sender as a threaded email reply.

8. Any further replies are automatically added to the same conversation.

---

# Quick Reference

| Action | Location |
|---|---|
| Login as admin | `/login` |
| Admin email | `admin@demo.example` |
| Admin password | `demopass123` |
| Team inbox | `/inbox` |
| Customer chat widget | `/demo` (use separate/incognito browser) |
| Knowledge base edit | `/kb` |
| Public knowledge base | `/kb/public/demo` |
| Test email | Send to `sssid0708@gmail.com` |
| Invite teammate | `/settings/team` |

---

# Additional Testing

You can test:

- Writing and editing knowledge base articles
- Checking AI summaries on conversations
- Managing team members
- Handling multiple support channels from a unified inbox
