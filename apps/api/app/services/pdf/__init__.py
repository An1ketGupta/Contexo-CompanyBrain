"""PDF generation adapters.

`render_html_to_pdf` (WeasyPrint) for HTML we author, `convert_docx_to_pdf`
(Gotenberg) for a `.docx` somebody else already filled.

Filling a document is not this package's job. That lives in
`services/documents/generation`, which splices values into recorded positions
without a templating engine — see `renderer.py`'s module docstring for why the
docxtpl path that used to live here was removed.
"""
from app.services.pdf.renderer import (
    PdfRenderError,
    PdfRenderUnavailable,
    convert_docx_to_pdf,
    render_html_to_pdf,
)

__all__ = [
    "PdfRenderError",
    "PdfRenderUnavailable",
    "convert_docx_to_pdf",
    "render_html_to_pdf",
]
