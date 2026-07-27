---
title: Using the developer API
category: Integrations
order: 7
tags: [api, keys, developer, http, integration, webhook]
---

The Contexo API lets you query your knowledge base and ingest documents from any system that can speak HTTP. It's available on Team and Business plans.

## Get an API key

1. Go to **Settings → API keys** (admins only).
2. Click **Create key**.
3. Give the key a label (e.g., `slack-bot`, `ci-pipeline`) so you can revoke just that one later.
4. Copy the key shown — it's displayed **once**. After you leave the page, only the prefix is stored; lose the full key and you'll need to create a new one.

API keys are workspace-scoped and use the permissions of the admin who created them.

## Authenticate

Send the key as a Bearer token on every request:

```
Authorization: Bearer cb_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

The base URL is `https://api.companybrain.app/v1`.

## Ask a question

```bash
curl -X POST https://api.companybrain.app/v1/chat \
  -H "Authorization: Bearer $CB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is our refund policy?"
  }'
```

The response includes the model's reply and an array of citations with `document_id`, `document_name`, and the chunk excerpt that was used.

## Ingest a document

```bash
curl -X POST https://api.companybrain.app/v1/documents \
  -H "Authorization: Bearer $CB_API_KEY" \
  -F "file=@handbook.pdf" \
  -F "tags=hr,onboarding"
```

The response includes the document ID and an initial status. Document processing is asynchronous — poll `GET /v1/documents/{id}` until status is `ready` (typically under a minute).

## Webhooks

Configure outgoing webhooks at **Settings → Webhooks**. Events that fire:

- `document.processed` — a document finished embedding.
- `document.failed` — ingestion failed; the payload includes the reason.
- `chat.completed` — an API or in-app chat finished. Useful for usage tracking.

All webhook payloads are signed with HMAC-SHA256 using your webhook secret; verify the `X-Companybrain-Signature` header on receipt.

## Rate limits

- **600 requests / minute / key** for chat endpoints.
- **60 requests / minute / key** for ingest endpoints.

Hitting a limit returns `429 Too Many Requests` with a `Retry-After` header.

## Revoke a key

**Settings → API keys → ⋯ → Revoke**. The key stops working immediately; anything running it sees `401 Unauthorized` on the next request.
