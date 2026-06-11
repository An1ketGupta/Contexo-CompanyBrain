---
title: What file types are supported?
category: Documents
order: 3
tags: [pdf, docx, xlsx, pptx, txt, md, html, csv, files, formats]
---

Company Brain accepts the following file types:

| Format | Extension | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Best for handbooks, contracts, scanned reports. OCR is applied to image-only PDFs. |
| Word | `.docx` | Headings and lists are preserved. Old `.doc` is not supported — export as `.docx` first. |
| Excel | `.xlsx` | Each sheet is ingested as its own section with headers retained. |
| PowerPoint | `.pptx` | Slide titles and speaker notes are extracted; embedded images are skipped. |
| Plain text | `.txt`, `.md` | Markdown headings become section anchors for citations. |
| HTML | `.html` | Scripts, styles, and navigation are stripped — only the content survives. |
| CSV | `.csv` | Each row is ingested with column headers as context. |
| Webpage | (via extension) | Live URLs added via the **"Add to Brain"** Chrome extension. |
| Email | (via integration) | Forwarded emails through your inbound address. |

## What's *not* supported

- Image-only files (`.jpg`, `.png`) — extract the text yourself first, or save as a PDF and let our OCR handle it.
- Audio/video — transcribe with a tool of your choice, then upload the transcript as text or markdown.
- Legacy Office formats (`.doc`, `.xls`, `.ppt`) — open in Office or Google and **Save As** the modern variant.
- Encrypted or password-protected PDFs — remove the password before uploading.

## File size limit

**50 MB per file.** Long PDFs that brush the limit also tend to retrieve poorly because the answer gets buried — splitting them into chapter-level files usually improves results.

## Character limit

There is no character limit per file beyond the size cap, but very long documents (>1,000 pages) are split into ~10,000 chunks under the hood. Retrieval still works on these, but you'll get sharper results by uploading the table-of-contents-level pieces separately.
