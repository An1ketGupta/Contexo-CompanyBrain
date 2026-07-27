---
title: How do I upload documents?
category: Documents
order: 2
tags: [upload, documents, drag, drop, files, ingest]
---

Documents are how you teach Contexo about your company. Anything you upload becomes searchable context for every chat and every generated output.

## Upload from the Documents page

1. Open **Documents** from the sidebar.
2. Click **Upload** in the top right, or drag files anywhere on the page.
3. Pick one or more files. The first few KB are read immediately so we can validate the type.
4. Each file moves through three states in the table:
   - **Processing** — we're parsing, chunking, and embedding the text.
   - **Ready** — the document is now available for search.
   - **Failed** — something went wrong; hover the badge for the reason.

Most documents are ready in under a minute. Larger PDFs or slide decks can take a few minutes.

## Bulk upload

Drag a folder onto the Documents page to queue everything inside it at once. Subfolders are flattened — directory structure isn't preserved, but file names are.

## Add tags during upload

In the upload dialog you can attach tags (e.g., `hr`, `q3-2026`, `eng-only`). Tags become filters in chat: ask a question with a tag selected and we'll only search documents that carry it.

## Add a webpage with the Chrome extension

For Notion pages, public docs, or any web URL, use the **"Add to Brain"** Chrome extension. It captures the page text and ingests it just like a file upload — no PDF export needed.

## What if a file is huge?

Files up to **50 MB** are accepted. Larger files should be split first — long PDFs in particular often benefit from being broken into chapters so retrieval can surface the right section.

## What happens after upload?

Processing is asynchronous: closing the tab won't cancel anything, and you'll see the document flip to **Ready** the next time you load the page (or in real time if you keep it open). See *"How does the AI search my documents?"* for what happens to the text once it's been ingested.
