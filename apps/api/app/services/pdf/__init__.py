"""PDF generation adapters — see renderer.py for the public surface."""
from app.services.pdf.renderer import (
    PdfRenderError,
    PdfRenderUnavailable,
    render_docx_template_to_pdf,
    render_html_to_pdf,
)

__all__ = [
    "render_html_to_pdf",
    "render_docx_template_to_pdf",
    "PdfRenderError",
    "PdfRenderUnavailable",
]
