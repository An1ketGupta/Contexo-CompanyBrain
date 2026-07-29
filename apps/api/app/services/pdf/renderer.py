"""PDF generation adapters — HTML→PDF (WeasyPrint) and DOCX→PDF (Gotenberg).

  1. **HTML→PDF (WeasyPrint)** — semantic HTML through a fixed Jinja layout,
     with @page rules for headers/footers/page numbers and Unicode (₹,
     Devanagari) via system fonts in the Docker image.

  2. **DOCX→PDF (Gotenberg)** — converts an already-filled `.docx`. The
     sidecar keeps a ~1GB LibreOffice install out of the FastAPI image.

Note what is NOT here any more: the docxtpl path that rendered a customer's
`.docx` as Jinja source. It required the customer's document to be *valid
template source*, so a single hand-typed `{{ Signing Date }}` failed the whole
render with a parser error HR could not act on. Filling a document is now
positional splicing in `services/documents/generation`, which has no syntax to
get wrong; this module only converts the result.

Both paths return raw PDF bytes — the caller uploads and persists. Storage is
deliberately not wired in here so unit tests can render without a network
round-trip.

Failure modes:
    * `PdfRenderUnavailable` — the system/sidecar isn't configured.
    * `PdfRenderError` — the render attempt failed.
"""
from __future__ import annotations

import asyncio
import logging

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


# ── DOCX → PDF (Gotenberg) ─────────────────────────────────────────────────

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

