---
title: Troubleshooting document processing
category: Documents
order: 10
tags: [failed, retry, processing, error, stuck, slow, ingest]
---

If a document doesn't make it to **Ready**, it'll either be **Processing** for a while or land on **Failed** with a reason. Here's how to read the signals and what to try.

## Document is stuck on "Processing"

A typical document is ready in under a minute. If it's been more than 10 minutes:

1. Refresh the Documents page. State is realtime but a tab in the background occasionally misses the update.
2. If it's still processing, click **⋯ → Retry** on the row. We'll re-run only the parts that didn't finish — fully-embedded chunks aren't redone.
3. If retry doesn't help, our ingestion worker may be backed up. Check `status.companybrain.app` for incidents.

## Document failed — common reasons

**"Could not parse document"** — the file is corrupted, encrypted, or in a format we don't fully support (e.g., a `.doc` rather than `.docx`). Open it in the source tool, **Save As** the modern format, and re-upload.

**"Empty document body"** — we parsed the file but extracted no text. This happens with image-only PDFs that don't have an OCR layer, or with PowerPoints where everything lives inside images. Run the file through OCR (or paste the speaker notes into a `.md` file) and try again.

**"All chunks failed to embed"** — the embedding provider rejected the text. Usually a quota issue (transient, resolves within 5 minutes) or content that tripped a safety filter. Click **Retry**; if it fails twice, contact support.

**"File too large"** — the file is over the 50 MB cap. Split it into smaller pieces and upload each one.

## Partial success — some chunks embedded, some didn't

You'll see an orange **Partial** badge with a number like "247 / 263 chunks". The document is searchable for the chunks that succeeded; the rest are queued for retry. Click **Retry** to re-attempt the failed chunks immediately.

This usually fixes itself in a couple of minutes — most chunk failures are transient rate-limit hiccups.

## The document is ready but search doesn't find it

Three things to check:

1. **Open the document detail page** and use the in-document search bar. If the term shows up there, retrieval should find it too. If it doesn't, the term may have been stripped during parsing (page headers/footers, watermarks).
2. **Confirm you're not filtered to a tag** that excludes this document. The chat input shows the active tag filter as a chip.
3. **Try the document's own wording.** Semantic search bridges synonyms but isn't magic. If your handbook says "annual leave" and you keep asking about "vacation policy", the semantic bridge is fine — but for jargony industry terms, mirroring the source wording sharpens results.

## Realtime status didn't update

If the Documents page shows **Processing** but you know the worker is done (e.g., the chat is citing this document), reload the page. The realtime channel can drop on flaky networks; the next full fetch reconciles.

## Still stuck

Reach `support@companybrain.app` with the document ID (visible in the row's **⋯ → Copy ID** menu) and a brief description. Admins can see processing logs for failed documents under **Admin → KB Health → At-risk → document → Logs**.
