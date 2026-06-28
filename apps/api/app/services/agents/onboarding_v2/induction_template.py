"""Induction PDF — HTML/CSS template fed to WeasyPrint.

The induction document is the only Onboarding v2 PDF that is *not* generated
from a customer-uploaded template — it's LLM-synthesized from the company's
KB on the fly, so there's nothing to upload. We ship a single branded HTML
layout with @page rules for headers, footers, and page numbers.

Why HTML over a DOCX template here:
    The induction is bespoke per hire (sections come from N concurrent KB
    facets), so a fixed template wouldn't be useful — we'd just be filling
    it with a long blob of prose. HTML lets the LLM emit semantic sections
    we can style cleanly; WeasyPrint's @page rules give us the polish.

Keep this file small — the actual section content comes in as a list of
`{"heading": "...", "body": "..."}` dicts from the agent.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

_BASE_CSS = """
@page {
  size: A4;
  margin: 22mm 18mm 24mm 18mm;
  @top-left {
    content: string(org-name);
    font-family: 'Noto Sans', sans-serif;
    font-size: 9pt;
    color: #71717a;
  }
  @bottom-right {
    content: "Page " counter(page) " of " counter(pages);
    font-family: 'Noto Sans', sans-serif;
    font-size: 9pt;
    color: #71717a;
  }
  @bottom-left {
    content: "Induction — confidential";
    font-family: 'Noto Sans', sans-serif;
    font-size: 8pt;
    color: #a1a1aa;
  }
}

body {
  font-family: 'Noto Sans', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  color: #18181b;
  font-size: 11pt;
  line-height: 1.6;
}

.cover {
  page-break-after: always;
  text-align: left;
  padding-top: 40mm;
}

.cover .eyebrow {
  color: #71717a;
  font-size: 11pt;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 8mm;
}

.cover h1 {
  font-size: 32pt;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 6mm 0;
  color: #09090b;
}

.cover .subtitle {
  font-size: 14pt;
  color: #3f3f46;
  margin: 0 0 12mm 0;
}

.cover .meta {
  margin-top: 24mm;
  font-size: 11pt;
  color: #52525b;
}

.org-name {
  string-set: org-name content();
  color: #18181b;
  font-weight: 600;
}

h2 {
  font-size: 16pt;
  font-weight: 600;
  color: #09090b;
  margin-top: 18pt;
  margin-bottom: 8pt;
  page-break-after: avoid;
}

h3 {
  font-size: 12pt;
  font-weight: 600;
  color: #18181b;
  margin-top: 12pt;
  margin-bottom: 4pt;
}

p { margin: 0 0 8pt 0; }
ul, ol { margin: 4pt 0 10pt 18pt; padding: 0; }
li { margin-bottom: 4pt; }

.section { page-break-inside: avoid; }
.callout {
  border-left: 3px solid #18181b;
  padding: 8pt 12pt;
  background: #fafafa;
  margin: 10pt 0;
  font-size: 10.5pt;
}

.sources {
  margin-top: 18mm;
  padding-top: 8pt;
  border-top: 1px solid #e4e4e7;
  font-size: 9pt;
  color: #71717a;
}

.sources strong { color: #3f3f46; font-weight: 600; }
.sources ul { margin-left: 14pt; }
"""


def _para_to_html(text: str) -> str:
    """Turn an LLM-emitted markdown-ish blob into safe HTML. We deliberately
    accept only a small subset (paragraphs, bullets, bold, code) to keep the
    render predictable across LLM output styles."""
    if not text:
        return ""
    safe = _html.escape(text)
    # **bold**
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    # `code`
    safe = re.sub(r"`([^`]+)`", r"<code>\1</code>", safe)

    # Split into blocks: bullet list or paragraph.
    blocks = re.split(r"\n\s*\n", safe.strip())
    out: list[str] = []
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if all(ln.strip().startswith(("- ", "* ", "• ")) for ln in lines):
            items = "".join(f"<li>{ln.strip()[2:].strip()}</li>" for ln in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            joined = "<br/>".join(lines)
            out.append(f"<p>{joined}</p>")
    return "\n".join(out)


def render_induction_html(
    *,
    candidate_name: str,
    role_title: str,
    org_name: str,
    start_date: str,
    sections: list[dict[str, str]],
    sources: list[dict[str, Any]] | None = None,
    logo_data_url: str | None = None,
) -> str:
    """Build the full induction HTML for WeasyPrint.

    `sections` is the LLM's output: a list of {heading, body} dicts.
    `sources` is the "Based on:" footer list; omitted when empty.
    """
    safe_name = _html.escape(candidate_name)
    safe_role = _html.escape(role_title)
    safe_org = _html.escape(org_name)
    safe_start = _html.escape(start_date)

    logo_html = (
        f'<div style="margin-bottom: 14mm;"><img src="{logo_data_url}" '
        f'style="max-height: 18mm;" alt="{safe_org} logo" /></div>'
        if logo_data_url
        else ""
    )

    sections_html_parts: list[str] = []
    for s in sections or []:
        heading = _html.escape((s.get("heading") or "").strip())
        body = _para_to_html((s.get("body") or "").strip())
        if not heading and not body:
            continue
        sections_html_parts.append(
            f'<div class="section"><h2>{heading}</h2>{body}</div>'
        )
    sections_html = "\n".join(sections_html_parts)

    sources_html = ""
    if sources:
        unique_docs: dict[str, str] = {}
        for s in sources:
            doc_id = s.get("document_id")
            if doc_id and doc_id not in unique_docs:
                unique_docs[doc_id] = _html.escape(
                    (s.get("document_name") or "Untitled").strip()
                )
        if unique_docs:
            items = "".join(f"<li>{name}</li>" for name in unique_docs.values())
            sources_html = (
                '<div class="sources">'
                "<strong>Based on documents from your company knowledge base:</strong>"
                f"<ul>{items}</ul></div>"
            )

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<title>Induction — {safe_name}</title>
<style>{_BASE_CSS}</style>
</head><body>

<section class="cover">
  {logo_html}
  <p class="eyebrow"><span class="org-name">{safe_org}</span></p>
  <h1>Welcome, {safe_name}.</h1>
  <p class="subtitle">Everything you need to know as a {safe_role}.</p>
  <div class="callout">
    Starts {safe_start}. Read this end-to-end before Day&nbsp;1 — your manager
    will assume the contents are familiar in your first 1:1.
  </div>
  <div class="meta">
    Prepared for {safe_name} · {safe_role} · {safe_org}
  </div>
</section>

{sections_html}

{sources_html}

</body></html>
"""
