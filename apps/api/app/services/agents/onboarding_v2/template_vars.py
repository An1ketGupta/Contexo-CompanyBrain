"""Single source of truth for the variables the Onboarding v2 agent makes
available to customer DOCX templates.

Keep this in sync with `OnboardingV2Agent._build_render_context` and the
HR-facing variable reference doc. The analyzer reads this list to constrain
what the LLM can map blanks to, so a typo in either place would cause the
analyzer and the agent to disagree about what a template can use.
"""
from __future__ import annotations

from typing import TypedDict


class TemplateVar(TypedDict):
    name: str          # the {{ name }} placeholder identifier
    label: str         # human label for the UI dropdown
    description: str   # short explanation — also goes to the LLM prompt
    example: str       # sample value used for previews + LLM context


TEMPLATE_VARIABLES: list[TemplateVar] = [
    {
        "name": "candidate_name",
        "label": "Candidate name",
        "description": "Full name of the person being onboarded.",
        "example": "Aniket Gupta",
    },
    {
        "name": "candidate_email",
        "label": "Candidate email",
        "description": "Candidate's primary email address.",
        "example": "aniket@example.com",
    },
    {
        "name": "candidate_phone",
        "label": "Candidate phone",
        "description": "Candidate's contact phone number.",
        "example": "+91 98765 43210",
    },
    {
        "name": "role_title",
        "label": "Role title",
        "description": "Job title from the offer (e.g. FullStack Developer).",
        "example": "FullStack Developer",
    },
    {
        "name": "designation",
        "label": "Designation",
        "description": "Internal designation if different from role title.",
        "example": "Senior Software Engineer",
    },
    {
        "name": "ctc",
        "label": "CTC (formatted)",
        "description": "Cost-to-company, pre-formatted with currency (e.g. 'INR 20,00,000.00').",
        "example": "INR 20,00,000.00",
    },
    {
        "name": "ctc_amount",
        "label": "CTC amount (numeric)",
        "description": "Raw numeric CTC value (no currency symbol).",
        "example": "2000000",
    },
    {
        "name": "ctc_currency",
        "label": "CTC currency",
        "description": "Currency code (INR, USD, etc.).",
        "example": "INR",
    },
    {
        "name": "start_date",
        "label": "Start date",
        "description": "ISO start date (YYYY-MM-DD).",
        "example": "2026-06-30",
    },
    {
        "name": "work_location",
        "label": "Work location",
        "description": "Office location or 'Remote'.",
        "example": "Remote",
    },
    {
        "name": "probation_period_months",
        "label": "Probation period (months)",
        "description": "Probation duration in months (integer).",
        "example": "3",
    },
    {
        "name": "reporting_manager_name",
        "label": "Reporting manager name",
        "description": "Full name of the candidate's reporting manager.",
        "example": "Naincy Sharma",
    },
    {
        "name": "reporting_manager_email",
        "label": "Reporting manager email",
        "description": "Reporting manager's email address.",
        "example": "naincy@example.com",
    },
    {
        "name": "company_name",
        "label": "Company name",
        "description": "Short / display name of the hiring company.",
        "example": "NirnayaIQ",
    },
    {
        "name": "company_legal_name",
        "label": "Company legal name",
        "description": "Registered legal entity name (for contract preambles).",
        "example": "NirnayaIQ Technologies Pvt. Ltd.",
    },
    {
        "name": "company_address",
        "label": "Company registered address",
        "description": "Full registered office address.",
        "example": "WeWork, Bengaluru, India",
    },
    {
        "name": "today_date",
        "label": "Today's date",
        "description": "Date the document is generated (ISO format).",
        "example": "2026-06-28",
    },
    {
        "name": "jurisdiction",
        "label": "Jurisdiction",
        "description": "Governing-law jurisdiction (typically a state or country).",
        "example": "Karnataka, India",
    },
    {
        "name": "hr_signature_block",
        "label": "HR signature location",
        "description": (
            "Place this where HR's signature should appear on the document. "
            "Renders as a sentinel marker (not visible text) that the e-sign "
            "service replaces with HR's drawn/typed signature. Optional — "
            "if omitted, the signature is placed at the bottom of the last page."
        ),
        "example": "",
    },
    {
        "name": "candidate_signature_block",
        "label": "Candidate signature location",
        "description": (
            "Place this where the candidate's signature should appear. Same "
            "sentinel-marker mechanism as hr_signature_block. Optional."
        ),
        "example": "",
    },
]

# Rendered values for the two signature-block variables — literal sentinel
# strings, not real data. apps/esign's pdf_sign.py searches the rendered PDF
# for these exact strings (see SIGNATURE_MARKERS in that module) to find
# where to stamp each signer's signature. Low-collision by construction —
# a lozenge-wrapped all-caps tag no real document text would contain.
SIGNATURE_BLOCK_MARKERS: dict[str, str] = {
    "hr_signature_block": "◇SIGN:HR◇",
    "candidate_signature_block": "◇SIGN:CANDIDATE◇",
}


def get_variable_names() -> list[str]:
    """Just the {{ name }} identifiers — for fast lookup / validation."""
    return [v["name"] for v in TEMPLATE_VARIABLES]


def get_variable_by_name(name: str) -> TemplateVar | None:
    for v in TEMPLATE_VARIABLES:
        if v["name"] == name:
            return v
    return None


# Sample context used for template previews and for the analyzer's
# dry-run validation. Mirrors what `_build_render_context` produces at runtime
# but with stable, obviously-fake values so a preview is never mistaken for a
# real document.
PREVIEW_SAMPLE_CONTEXT: dict[str, object] = {
    **{v["name"]: v["example"] for v in TEMPLATE_VARIABLES},
    "ctc_amount": 2_000_000.0,
    "probation_period_months": 3,
    "ctc_breakdown": {"basic": 1_000_000, "hra": 500_000, "special_allowance": 500_000},
}
