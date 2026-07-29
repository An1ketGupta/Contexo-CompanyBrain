"""End-to-end wiring of detection → prompt → model → validated analysis.

`test_document_analyzer.py` covers the validation surface in isolation. This
covers the seam around it: that a real DOCX flows through the detector into the
prompt, that the prompt actually contains what the model needs, and that a
model response comes back out as slots anchored at real characters.

The model is stubbed. The point is the wiring, not the model.
"""
from __future__ import annotations

import io
import json

import pytest
from docx import Document

from app.services.documents.analysis import analyzer as analyzer_mod
from app.services.documents.analysis.analyzer import (
    TemplateAnalysisError,
    analyze_template,
)
from app.services.documents.analysis.detector import find_all_candidates
from app.services.llm.client import LLMError
from app.services.llm.types import LLMResponse

ALLOWED = ["offer_letter", "nda", "employment_agreement"]

LINES = [
    "Dear Rahul,",
    "We are pleased to offer you the position of Software Engineer.",
    "Joining Date: 15 August 2026",
    "Annual Salary: Rs 12,00,000",
    "Reporting Manager: John Smith",
]


def _offer_letter_docx() -> bytes:
    doc = Document()
    for line in LINES:
        doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


class _StubClient:
    """Captures the prompt it was given and replays a canned response."""

    def __init__(self, payload, *, raise_error: Exception | None = None):
        self.payload = payload
        self.raise_error = raise_error
        self.system_prompt: str | None = None
        self.user_prompt: str | None = None

    async def complete(self, messages, **kwargs):
        if self.raise_error:
            raise self.raise_error
        self.system_prompt = kwargs.get("system_extra")
        self.user_prompt = messages[0].content
        body = (
            self.payload
            if isinstance(self.payload, str)
            else json.dumps(self.payload)
        )
        return LLMResponse(text=body)


@pytest.fixture
def stub(monkeypatch):
    def _install(payload, *, raise_error=None):
        client = _StubClient(payload, raise_error=raise_error)
        monkeypatch.setattr(analyzer_mod, "get_llm_client", lambda: client)
        return client

    return _install


def _canned_response(candidates):
    """A plausible model answer for the offer letter above."""
    by_label = {c.label_hint: c.candidate_id for c in candidates}
    return {
        "document_type": "offer_letter",
        "document_type_confidence": 0.97,
        "variables": [
            {
                "internal_name": "candidate_name",
                "display_name": "Candidate Name",
                "data_type": "text",
                "required": True,
            },
            {
                "internal_name": "joining_date",
                "display_name": "Joining Date",
                "data_type": "date",
                "required": True,
            },
            {
                "internal_name": "annual_salary",
                "display_name": "Annual Salary",
                "data_type": "currency",
                "required": True,
            },
            {
                "internal_name": "reporting_manager",
                "display_name": "Reporting Manager",
                "data_type": "manager",
                "required": False,
            },
        ],
        "assignments": [
            {"fill_point_id": by_label["Joining Date"], "internal_name": "joining_date", "confidence": 0.94},
            {"fill_point_id": by_label["Annual Salary"], "internal_name": "annual_salary", "confidence": 0.96},
            {"fill_point_id": by_label["Reporting Manager"], "internal_name": "reporting_manager", "confidence": 0.9},
        ],
        "literals": [
            {"paragraph": 0, "text": "Rahul", "internal_name": "candidate_name", "confidence": 0.99},
        ],
    }


@pytest.mark.asyncio
async def test_full_pass_over_a_filled_offer_letter(stub):
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    stub(_canned_response(candidates))

    result = await analyze_template(
        docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
    )

    assert result.document_type_key == "offer_letter"
    assert result.document_type_confidence == 0.97

    names = {v.internal_name for v in result.variables}
    assert names == {"candidate_name", "joining_date", "annual_salary", "reporting_manager"}

    # The unlabelled name was found by the literal pass, at real offsets.
    literal = [s for s in result.slots if s.internal_name == "candidate_name"]
    assert len(literal) == 1
    assert LINES[0][literal[0].start_offset:literal[0].end_offset] == "Rahul"


@pytest.mark.asyncio
async def test_types_survive_the_round_trip(stub):
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    stub(_canned_response(candidates))

    result = await analyze_template(
        docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
    )
    by_name = {v.internal_name: v for v in result.variables}
    assert by_name["joining_date"].data_type == "date"
    assert by_name["annual_salary"].data_type == "currency"
    assert by_name["reporting_manager"].is_required is False


@pytest.mark.asyncio
async def test_prompt_contains_what_the_model_needs(stub):
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    client = stub(_canned_response(candidates))

    await analyze_template(
        docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
    )

    prompt = client.user_prompt
    # The allowed vocabulary, so the model can't invent a document type.
    assert "offer_letter" in prompt
    # Numbered paragraphs, so literals can cite a position.
    assert "[0] Dear Rahul," in prompt
    # Located fill-points with their labels and occupancy.
    assert "Joining Date" in prompt
    assert "holds-a-value" in prompt
    # The instruction not to rewrite anything.
    assert "NEVER rewrite" in client.system_prompt


@pytest.mark.asyncio
async def test_fenced_json_is_tolerated(stub):
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    stub("```json\n" + json.dumps({"document_type": "nda"}) + "\n```")

    result = await analyze_template(
        docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
    )
    assert result.document_type_key == "nda"


@pytest.mark.asyncio
async def test_unparseable_response_raises_actionable_error(stub):
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    stub("I'm sorry, I can't help with that.")

    with pytest.raises(TemplateAnalysisError) as exc:
        await analyze_template(
            docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
        )
    assert "manually" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_llm_outage_raises_actionable_error(stub):
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    stub({}, raise_error=LLMError("upstream 503"))

    with pytest.raises(TemplateAnalysisError) as exc:
        await analyze_template(
            docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
        )
    # HR must be told they can carry on without the model.
    assert "manually" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_model_cannot_smuggle_in_a_rewritten_clause(stub):
    """A literal quoting text that is not in the document — here a reworded
    salary clause — must produce nothing at all."""
    docx = _offer_letter_docx()
    candidates = find_all_candidates(docx)
    stub({
        "variables": [{"internal_name": "salary_clause", "display_name": "Salary Clause"}],
        "literals": [{
            "paragraph": 3,
            "text": "Annual Salary: Rs 15,00,000 plus equity",
            "internal_name": "salary_clause",
        }],
    })

    result = await analyze_template(
        docx_bytes=docx, candidates=candidates, allowed_type_keys=ALLOWED
    )
    assert result.rejected_literals == ["Annual Salary: Rs 15,00,000 plus equity"]
    assert not [s for s in result.slots if s.internal_name == "salary_clause"]
