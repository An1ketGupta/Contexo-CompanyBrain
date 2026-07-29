"""Vocabularies shared by the document pipeline.

Every tuple here mirrors a CHECK constraint in
`supabase/migrations/099_document_generation_pipeline.sql`. They are duplicated
in Python because the analyzer, the validation engine, and the Pydantic request
models all need to reason about them without a database round-trip — but the
database is the authority. If you widen one, widen the other in the same commit.

Note what is deliberately NOT here: the list of document types. Those live in
the `document_types` table precisely so adding one is an INSERT rather than a
code change, which was the single biggest rigidity in the pipeline this
replaces (`documents.template_kind` was a CHECK; 076 widened it and 092
narrowed it back).
"""
from __future__ import annotations

from typing import Final

# ── Variable data types ────────────────────────────────────────────────────
# Drives which validator runs in the validation engine and which input control
# the template builder renders.

DATA_TYPE_TEXT: Final = "text"
DATA_TYPE_EMAIL: Final = "email"
DATA_TYPE_PHONE: Final = "phone"
DATA_TYPE_CURRENCY: Final = "currency"
DATA_TYPE_DATE: Final = "date"
DATA_TYPE_NUMBER: Final = "number"
DATA_TYPE_BOOLEAN: Final = "boolean"
DATA_TYPE_ADDRESS: Final = "address"
DATA_TYPE_COUNTRY: Final = "country"
DATA_TYPE_STATE: Final = "state"
DATA_TYPE_CITY: Final = "city"
DATA_TYPE_DESIGNATION: Final = "designation"
DATA_TYPE_DEPARTMENT: Final = "department"
DATA_TYPE_MANAGER: Final = "manager"
DATA_TYPE_COMPANY: Final = "company"
DATA_TYPE_SIGNATURE_BLOCK: Final = "signature_block"
DATA_TYPE_CUSTOM: Final = "custom"

DATA_TYPES: Final[tuple[str, ...]] = (
    DATA_TYPE_TEXT,
    DATA_TYPE_EMAIL,
    DATA_TYPE_PHONE,
    DATA_TYPE_CURRENCY,
    DATA_TYPE_DATE,
    DATA_TYPE_NUMBER,
    DATA_TYPE_BOOLEAN,
    DATA_TYPE_ADDRESS,
    DATA_TYPE_COUNTRY,
    DATA_TYPE_STATE,
    DATA_TYPE_CITY,
    DATA_TYPE_DESIGNATION,
    DATA_TYPE_DEPARTMENT,
    DATA_TYPE_MANAGER,
    DATA_TYPE_COMPANY,
    DATA_TYPE_SIGNATURE_BLOCK,
    DATA_TYPE_CUSTOM,
)

# Types whose value is not candidate data and must therefore be skipped by the
# validation engine's "is this field populated?" pass. A signature block emits a
# sentinel marker that apps/esign searches for in the rendered PDF; there is no
# upstream source to resolve it from and a missing one is not an error.
NON_DATA_TYPES: Final[frozenset[str]] = frozenset({DATA_TYPE_SIGNATURE_BLOCK})


# ── Slot actions ───────────────────────────────────────────────────────────
# How the renderer writes a value at an anchor.

ACTION_REPLACE_SPAN: Final = "replace_span"
ACTION_INSERT_AFTER_LABEL: Final = "insert_after_label"
ACTION_INSERT_EMPTY_CELL: Final = "insert_empty_cell"

SLOT_ACTIONS: Final[tuple[str, ...]] = (
    ACTION_REPLACE_SPAN,
    ACTION_INSERT_AFTER_LABEL,
    ACTION_INSERT_EMPTY_CELL,
)

# For these the anchor is an insertion point, so start_offset == end_offset and
# there is no original text to overwrite.
INSERTION_ACTIONS: Final[frozenset[str]] = frozenset({
    ACTION_INSERT_AFTER_LABEL,
    ACTION_INSERT_EMPTY_CELL,
})


# ── Review status (variables and slots share this vocabulary) ──────────────
# `rejected` rows are kept rather than deleted so that re-running analysis does
# not keep re-proposing a mapping a human already turned down.

STATUS_PROPOSED: Final = "proposed"
STATUS_CONFIRMED: Final = "confirmed"
STATUS_REJECTED: Final = "rejected"

REVIEW_STATUSES: Final[tuple[str, ...]] = (
    STATUS_PROPOSED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
)

SOURCE_AI: Final = "ai"
SOURCE_MANUAL: Final = "manual"
SOURCES: Final[tuple[str, ...]] = (SOURCE_AI, SOURCE_MANUAL)


# ── Paragraph kinds ────────────────────────────────────────────────────────

PARAGRAPH_KINDS: Final[tuple[str, ...]] = ("body", "table", "header", "footer")


# ── Template version analysis lifecycle ────────────────────────────────────

ANALYSIS_PENDING: Final = "pending"
ANALYSIS_RUNNING: Final = "analyzing"
ANALYSIS_COMPLETED: Final = "completed"
ANALYSIS_FAILED: Final = "failed"
ANALYSIS_MANUAL: Final = "manual"

ANALYSIS_STATUSES: Final[tuple[str, ...]] = (
    ANALYSIS_PENDING,
    ANALYSIS_RUNNING,
    ANALYSIS_COMPLETED,
    ANALYSIS_FAILED,
    ANALYSIS_MANUAL,
)


# ── Generation lifecycle ───────────────────────────────────────────────────

GEN_PENDING: Final = "pending"
GEN_VALIDATION_FAILED: Final = "validation_failed"
GEN_GENERATING: Final = "generating"
GEN_GENERATION_FAILED: Final = "generation_failed"
GEN_GENERATED: Final = "generated"
GEN_APPROVED: Final = "approved"
GEN_REJECTED: Final = "rejected"
GEN_SENDING: Final = "sending"
GEN_SENT: Final = "sent"
GEN_SEND_FAILED: Final = "send_failed"

GENERATION_STATUSES: Final[tuple[str, ...]] = (
    GEN_PENDING,
    GEN_VALIDATION_FAILED,
    GEN_GENERATING,
    GEN_GENERATION_FAILED,
    GEN_GENERATED,
    GEN_APPROVED,
    GEN_REJECTED,
    GEN_SENDING,
    GEN_SENT,
    GEN_SEND_FAILED,
)

# A generation in one of these states produced no artifact HR can act on, so
# the UI offers "retry" rather than "download / approve".
GENERATION_FAILURE_STATUSES: Final[frozenset[str]] = frozenset({
    GEN_VALIDATION_FAILED,
    GEN_GENERATION_FAILED,
    GEN_SEND_FAILED,
})


# ── Template lifecycle ─────────────────────────────────────────────────────

TEMPLATE_DRAFT: Final = "draft"
TEMPLATE_ACTIVE: Final = "active"
TEMPLATE_ARCHIVED: Final = "archived"

TEMPLATE_STATUSES: Final[tuple[str, ...]] = (
    TEMPLATE_DRAFT,
    TEMPLATE_ACTIVE,
    TEMPLATE_ARCHIVED,
)


# ── File formats ───────────────────────────────────────────────────────────

FORMAT_DOCX: Final = "docx"
FORMAT_PDF: Final = "pdf"
GENERATED_FORMATS: Final[tuple[str, ...]] = (FORMAT_DOCX, FORMAT_PDF)

MIME_DOCX: Final = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_PDF: Final = "application/pdf"

# DOCX is the primary format the pipeline supports end-to-end. PDF templates are
# accepted for storage and manual field definition but cannot be span-filled,
# because a PDF has no run model to splice into.
UPLOADABLE_MIMES: Final[tuple[str, ...]] = (MIME_DOCX, MIME_PDF)


# ── Signature blocks ───────────────────────────────────────────────────────
# A `signature_block` variable does not carry candidate data. It renders as a
# sentinel string that apps/esign locates in the finished PDF (with PyMuPDF's
# search_for) to position each signer's field, then whites out before stamping.
#
# These MUST stay byte-identical to SIGNATURE_MARKERS in
# apps/esign/app/field_placement.py. They live in two apps that deploy
# independently, so `test_document_validation.py` asserts the two agree rather
# than trusting a comment. A drifted marker means signatures land in the wrong
# place on a signed contract.

SIGNER_HR: Final = "hr"
SIGNER_CANDIDATE: Final = "candidate"
SIGNERS: Final[tuple[str, ...]] = (SIGNER_HR, SIGNER_CANDIDATE)

SIGNATURE_MARKERS: Final[dict[str, str]] = {
    SIGNER_HR: "◇SIGN:HR◇",
    SIGNER_CANDIDATE: "◇SIGN:CANDIDATE◇",
}

# Substrings that identify a signature block as the employer's rather than the
# candidate's, when the variable does not declare a signer explicitly.
_HR_SIGNER_HINTS: Final[tuple[str, ...]] = (
    "hr", "employer", "company", "authorised", "authorized", "organisation",
    "organization", "manager", "director", "behalf",
)


def signer_for_variable(variable: dict) -> str:
    """Which signer a `signature_block` variable belongs to.

    Prefers an explicit `{"signer": "hr"}` in `validation_rules` — that is what
    the template builder writes when HR picks a signer. Falls back to reading
    the variable's name, so an AI-proposed `hr_signature` works before anyone
    has configured it. Defaults to the candidate, because a document with one
    unlabelled signature block is far more often the recipient's.
    """
    rules = variable.get("validation_rules") or {}
    declared = rules.get("signer")
    if isinstance(declared, str) and declared.strip().lower() in SIGNATURE_MARKERS:
        return declared.strip().lower()

    haystack = f"{variable.get('internal_name', '')} {variable.get('display_name', '')}".lower()
    if any(hint in haystack for hint in _HR_SIGNER_HINTS):
        return SIGNER_HR
    return SIGNER_CANDIDATE


# ── Detection tuning ───────────────────────────────────────────────────────

# A variable detected at or above this confidence is still shown to HR, but the
# builder pre-ticks it. Below it, HR must make an explicit choice. Deliberately
# a float compared against `doc_template_variables.confidence` rather than a
# high/medium/low bucket, so an org can move the line without a migration.
DEFAULT_CONFIRM_THRESHOLD: Final[float] = 0.85


# ── Audit actions ──────────────────────────────────────────────────────────
# Conventional values used by this package. `document_audit_logs.action` has no
# CHECK constraint on purpose, so recording a new kind of event never requires a
# migration — these constants exist for consistency, not enforcement.

AUDIT_TEMPLATE_CREATED: Final = "template.created"
AUDIT_TEMPLATE_UPDATED: Final = "template.updated"
AUDIT_TEMPLATE_ARCHIVED: Final = "template.archived"
AUDIT_VERSION_UPLOADED: Final = "template_version.uploaded"
AUDIT_VERSION_PROMOTED: Final = "template_version.promoted"
AUDIT_ANALYSIS_STARTED: Final = "analysis.started"
AUDIT_ANALYSIS_COMPLETED: Final = "analysis.completed"
AUDIT_ANALYSIS_FAILED: Final = "analysis.failed"
AUDIT_VARIABLE_CONFIRMED: Final = "variable.confirmed"
AUDIT_VARIABLE_REJECTED: Final = "variable.rejected"
AUDIT_VARIABLE_UPDATED: Final = "variable.updated"
AUDIT_VARIABLE_CREATED: Final = "variable.created"
AUDIT_SLOT_CONFIRMED: Final = "slot.confirmed"
AUDIT_SLOT_REJECTED: Final = "slot.rejected"
AUDIT_SLOT_CREATED: Final = "slot.created"
AUDIT_MAPPING_UPDATED: Final = "mapping.updated"
AUDIT_VALIDATION_FAILED: Final = "validation.failed"
AUDIT_GENERATION_STARTED: Final = "generation.started"
AUDIT_GENERATION_COMPLETED: Final = "generation.completed"
AUDIT_GENERATION_FAILED: Final = "generation.failed"
AUDIT_DOCUMENT_APPROVED: Final = "document.approved"
AUDIT_DOCUMENT_REJECTED: Final = "document.rejected"
AUDIT_DOCUMENT_SENT: Final = "document.sent"
AUDIT_DOCUMENT_SEND_FAILED: Final = "document.send_failed"


# ── Entity types for audit rows ────────────────────────────────────────────

ENTITY_TEMPLATE: Final = "doc_template"
ENTITY_VERSION: Final = "doc_template_version"
ENTITY_VARIABLE: Final = "doc_template_variable"
ENTITY_SLOT: Final = "doc_template_slot"
ENTITY_MAPPING: Final = "doc_variable_mapping"
ENTITY_GENERATED_DOCUMENT: Final = "generated_document"
