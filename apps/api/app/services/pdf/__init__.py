"""PDF generation adapters.

Two DOCX fill paths, deliberately separate — see `renderer.py` (Jinja/docxtpl,
for hand-authored templates) and `slot_renderer.py` (direct positional fill, for
customer-uploaded documents that were never written as templates).

`slot_renderer` is NOT re-exported here: it imports from
`app.services.agents.onboarding_v2`, so pulling it into this package's import
graph would make `app.services.pdf` and the onboarding agent mutually
dependent. Import it by module path instead.
"""
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
