---
title: How does the AI search my documents?
category: AI & Search
order: 4
tags: [search, ai, embeddings, rag, vector, hybrid, retrieval, citations]
---

When you ask Contexo a question, it doesn't pre-read everything you've uploaded. Instead, the model **decides what to look up** and we run a focused search across your documents for each lookup.

## Three stages

1. **Decision** — the LLM reads your message and picks 2–4 search queries it thinks would help (e.g., for *"Write a launch email"*, it might search for `product launch tone`, `Q3 release notes`, and `target audience persona`).
2. **Retrieval** — each query runs against your knowledge base using **hybrid search**: a semantic search (vector embeddings) and a keyword search (full-text) combined with Reciprocal Rank Fusion.
3. **Generation** — the top results from all queries are stitched together as context, and the model writes its answer grounded in them.

## Why hybrid?

Vector search is great at *meaning* — it finds the right idea even if your document uses different words. Keyword search is great at *names* — product names, person names, error codes. We always run both because either alone misses cases the other catches.

## What's a chunk?

Every document is split into ~500-token pieces during ingestion. Each piece is embedded and indexed separately. When you see a citation in chat, it's pointing to the specific chunk that influenced the answer, not the whole document.

## Citations

Every reply lists the documents that were searched and the specific passages used. Click any citation to jump straight to the source chunk in the original document.

If a reply has **no citations** but does include facts about your company, treat them as the model's general knowledge — it may have inferred from context or from things it already knows. Citations are the audit trail; an answer with none should be double-checked.

## Why no results?

If chat answers come back generic, three things to check:

- **Is the document actually ready?** Open the Documents page and confirm the status is **Ready**, not Processing or Failed.
- **Is the question phrased like the documents are?** If your handbook says "annual leave" and you ask about "vacation policy", the semantic search will bridge it — but trying the document's own wording can sharpen results.
- **Did you filter to a tag that excludes the answer?** Tags scope retrieval; remove the filter and try again.

## Knowledge gaps

When the model is asked something but retrieval doesn't return strong matches, we log the question as a **knowledge gap**. Admins can review the gap list at **Admin → KB Health** and use it to prioritize what to upload next.
