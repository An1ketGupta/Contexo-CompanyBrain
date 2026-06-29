"""PDF generation adapters — see renderer.py for the public surface."""
from app.services.pdf.renderer import (
    PdfRenderError,
    PdfRenderUnavailable,
    TemplateVariableError,
    convert_docx_to_pdf,
    fill_docx_template,
    render_docx_template_to_pdf,
    render_html_to_pdf,
)

__all__ = [
    "render_html_to_pdf",
    "render_docx_template_to_pdf",
    "convert_docx_to_pdf",
    "fill_docx_template",
    "PdfRenderError",
    "PdfRenderUnavailable",
    "TemplateVariableError",
]
