"""PDF generation adapters — HTML→PDF (WeasyPrint) and DOCX-template→PDF
(docxtpl + Gotenberg).

Two distinct rendering paths because the Onboarding v2 agent has two distinct
content shapes:

  1. **HTML→PDF (WeasyPrint)** — used for the LLM-synthesized induction
     document and for any in-product template the team wants to ship with
     full brand control. We send semantic HTML through a fixed Jinja layout
     and WeasyPrint handles @page rules (headers/footers/page numbers) and
     Unicode (₹, Devanagari) via system fonts in the Docker image.

  2. **DOCX-template→PDF (docxtpl + Gotenberg)** — used for customer-uploaded
     LOI / Appointment Letter / NDA templates. HR teams maintain these in
     Word with their lawyer's exact clause wording; we fill the placeholders
     with docxtpl (preserves 100% of the original formatting), then convert
     to PDF via the Gotenberg sidecar (LibreOffice route). The sidecar keeps
     a ~1GB LibreOffice install out of the FastAPI image.

Both paths return raw PDF bytes — the caller is responsible for uploading to
Supabase Storage and persisting the row. We deliberately don't wire storage
in here so unit tests can render without a network round-trip.

Failure modes:
    * `PdfRenderUnavailable` — the system/sidecar isn't configured. The agent
      surfaces this as a blocked_missing_template-style error so HR sees
      "PDF service unavailable" instead of a generic 500.
    * `PdfRenderError` — render attempt failed (bad template syntax, malformed
      HTML, Gotenberg upstream error). The agent logs + retries via Inngest.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class PdfRenderError(RuntimeError):
    """Render attempt failed deterministically — bad template, malformed
    context, or upstream Gotenberg 4xx. Not retryable in most cases."""


class PdfRenderUnavailable(RuntimeError):
    """The dependency required to render this PDF isn't installed/configured.
    For WeasyPrint this means the wheel isn't importable; for Gotenberg this
    means GOTENBERG_URL isn't set. The agent treats this as a blocked-state
    so the operator can fix the deployment before retrying."""


# ── HTML → PDF (WeasyPrint) ────────────────────────────────────────────────

async def render_html_to_pdf(
    html: str,
    *,
    base_url: str | None = None,
) -> bytes:
    """Render an HTML string to PDF bytes via WeasyPrint.

    base_url controls resolution of relative URLs in the HTML (e.g. an
    <img src="/logo.png">). For our use case the org logo is fetched server-
    side and embedded as a data: URL, so base_url is typically None.

    WeasyPrint blocks on system fonts + Pango/Cairo native calls — we run it
    on a thread to keep the FastAPI event loop responsive. A 50-page induction
    PDF takes ~1-2s on Railway's standard tier; well within Inngest step
    timeouts.
    """
    try:
        # Imported lazily so a deployment that doesn't need WeasyPrint (e.g.
        # local dev that only exercises the DOCX path) boots cleanly even
        # when the system libs aren't installed.
        from weasyprint import HTML  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PdfRenderUnavailable(
            "WeasyPrint not installed. Add `weasyprint` to pyproject.toml "
            "and install the Pango/Cairo system libs (see api/Dockerfile)."
        ) from exc

    def _render() -> bytes:
        try:
            doc = HTML(string=html, base_url=base_url)
            return doc.write_pdf() or b""
        except Exception as exc:  # noqa: BLE001 — wrap for the caller
            raise PdfRenderError(f"WeasyPrint render failed: {exc}") from exc

    return await asyncio.to_thread(_render)


# ── DOCX-template → PDF (docxtpl + Gotenberg) ──────────────────────────────

async def fill_docx_template(
    *,
    template_bytes: bytes,
    context: dict[str, Any],
) -> bytes:
    """Render a customer-uploaded .docx template with Jinja2-style
    {{ placeholders }} via docxtpl. Returns the filled .docx bytes.

    Kept separate from the PDF conversion so callers can store the filled
    .docx alongside the PDF (handy for later edits + an audit trail of
    exactly what got sent).
    """
    try:
        from docxtpl import DocxTemplate  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PdfRenderUnavailable(
            "docxtpl not installed. Add `docxtpl` to pyproject.toml."
        ) from exc

    def _fill() -> bytes:
        try:
            tpl = DocxTemplate(io.BytesIO(template_bytes))
            tpl.render(context)
            out = io.BytesIO()
            tpl.save(out)
            return out.getvalue()
        except Exception as exc:  # noqa: BLE001
            raise PdfRenderError(f"docxtpl fill failed: {exc}") from exc

    return await asyncio.to_thread(_fill)


async def convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert a .docx to PDF via the Gotenberg sidecar's LibreOffice route.

    Gotenberg URL is read from `GOTENBERG_URL` (set in Railway alongside the
    sidecar). If the URL isn't configured we raise PdfRenderUnavailable so
    the agent treats it as a deployment problem, not a content one.
    """
    settings = get_settings()
    gotenberg_url = getattr(settings, "gotenberg_url", "") or ""
    if not gotenberg_url:
        raise PdfRenderUnavailable(
            "GOTENBERG_URL not configured. Provision the gotenberg sidecar "
            "and set GOTENBERG_URL=http://<service>:3000 in Railway."
        )

    endpoint = gotenberg_url.rstrip("/") + "/forms/libreoffice/convert"
    files = {
        # Gotenberg keys input on the filename: must end in .docx.
        "files": ("document.docx", docx_bytes, (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )),
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(endpoint, files=files)
    except httpx.TimeoutException as exc:
        raise PdfRenderError(f"Gotenberg timeout converting DOCX: {exc}") from exc
    except httpx.HTTPError as exc:
        raise PdfRenderError(f"Gotenberg transport error: {exc}") from exc

    if resp.status_code != 200:
        # Gotenberg returns useful detail in the body; cap to keep logs sane.
        snippet = resp.text[:500] if resp.text else "<empty>"
        raise PdfRenderError(
            f"Gotenberg returned {resp.status_code}: {snippet}"
        )

    pdf = resp.content
    if not pdf or not pdf.startswith(b"%PDF"):
        raise PdfRenderError("Gotenberg returned non-PDF payload.")
    return pdf


async def render_docx_template_to_pdf(
    *,
    template_bytes: bytes,
    context: dict[str, Any],
) -> tuple[bytes, bytes]:
    """End-to-end render: fill the .docx template with `context`, then
    convert to PDF via Gotenberg.

    Returns (filled_docx_bytes, pdf_bytes). The caller can store both; we
    keep the .docx around because it's the only artifact that's editable if
    HR wants to tweak wording before sending.
    """
    filled_docx = await fill_docx_template(
        template_bytes=template_bytes, context=context
    )
    pdf_bytes = await convert_docx_to_pdf(filled_docx)
    return filled_docx, pdf_bytes
