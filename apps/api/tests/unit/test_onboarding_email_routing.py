"""Which email a step's documents are announced with.

Bespoke copy — "the LOI is ready for your signature", "the Appointment Letter
and NDA are ready" — is earned by what a step renders, not by what its
`step_key` happens to be. Now that an org composes its own catalog, a step named
"Letter of Intent" that the org created itself has the key `letter_of_intent`,
and a step named "Joining Documents" has no built-in equivalent at all. Routing
on the literal keys sent both of those the appointment-bundle email, which told
HR that reference checks were complete and that an NDA was waiting for them when
neither was true.
"""
from __future__ import annotations

import pytest

import app.inngest  # noqa: F401  (import-order cycle; see test_onboarding_step_toggles)
from app.services.agents.onboarding_v2 import agent as agent_mod
from app.services.agents.onboarding_v2 import catalog
from app.services.agents.onboarding_v2.agent import OnboardingV2Agent

ORG = "org-1"
RUN = "run-1"
HR_USER = "user-hr"
HR_EMAIL = "hr@example.com"

RUN_ROW = {
    "id": RUN,
    "candidate_name": "Rahul Iyer",
    "role_title": "FullStack Developer",
    "start_date": "2026-07-08",
    "ctc_amount": 4000000,
    "ctc_currency": "INR",
    "triggered_by_user_id": HR_USER,
    "references_form_token": None,
}


def _step(step_key, *, document_type_key, label, bundle_key=None, bundle_label=None):
    return {
        "id": f"rs-{step_key}",
        "run_id": RUN,
        "org_id": ORG,
        "step_key": step_key,
        "kind": catalog.KIND_GENERATE,
        "label": label,
        "document_type_key": document_type_key,
        "bundle_key": bundle_key,
        "bundle_label": bundle_label,
        "position": 0,
        "signer_roles": [],
        "system_action": None,
        "status": catalog.STATUS_PENDING_HR_REVIEW,
    }


class _Settings:
    app_url = "https://app.example.com"


@pytest.fixture
def agent(monkeypatch):
    """An agent whose HR notification is captured instead of queued."""
    a = OnboardingV2Agent(org_id=ORG, run_id=RUN)
    a.sent: list[dict] = []

    async def fake_load_run():
        return dict(RUN_ROW)

    async def fake_send_email_event(**kwargs):
        a.sent.append(kwargs)

    async def fake_log_step(*args, **kwargs):
        return None

    class _Admin:
        def get_user_by_id(self, uid):
            return type("R", (), {"user": type("U", (), {"email": HR_EMAIL})()})()

    class _Client:
        auth = type("A", (), {"admin": _Admin()})()

    monkeypatch.setattr(a, "_load_run", fake_load_run)
    monkeypatch.setattr(a, "log_step", fake_log_step)
    monkeypatch.setattr(agent_mod, "send_email_event", fake_send_email_event)
    monkeypatch.setattr(agent_mod, "get_service_client", lambda: _Client())
    monkeypatch.setattr(agent_mod, "get_settings", lambda: _Settings())
    return a


# ── HR review notification ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seeded_loi_step_gets_the_loi_email(agent):
    step = _step("loi", document_type_key="letter_of_intent", label="Letter of intent")

    await agent._notify_hr_documents_ready([step])

    assert agent.sent[0]["event_type"] == "onboarding_loi_ready"


@pytest.mark.asyncio
async def test_org_recreated_loi_step_still_gets_the_loi_email(agent):
    """The reported bug: an org-created LOI step keyed `letter_of_intent`."""
    step = _step(
        "letter_of_intent",
        document_type_key="letter_of_intent",
        label="Letter of Intent",
    )

    await agent._notify_hr_documents_ready([step])

    assert agent.sent[0]["event_type"] == "onboarding_loi_ready"


@pytest.mark.asyncio
async def test_seeded_appointment_bundle_gets_the_bundle_email(agent):
    bundle = [
        _step(
            "appointment_letter",
            document_type_key="appointment_letter",
            label="Appointment letter",
            bundle_key=catalog.BUNDLE_APPOINTMENT,
            bundle_label="Appointment letter + NDA",
        ),
        _step(
            "nda",
            document_type_key="nda",
            label="NDA",
            bundle_key=catalog.BUNDLE_APPOINTMENT,
            bundle_label="Appointment letter + NDA",
        ),
    ]

    await agent._notify_hr_documents_ready(bundle)

    assert agent.sent[0]["event_type"] == "onboarding_offer_bundle_ready"


@pytest.mark.asyncio
async def test_org_composed_step_gets_the_generic_email_naming_its_documents(agent):
    bundle = [
        _step(
            "joining_documents",
            document_type_key="joining_kit",
            label="Joining kit",
            bundle_key="joining",
            bundle_label="Joining Documents",
        ),
        _step(
            "joining_documents_2",
            document_type_key="bank_mandate",
            label="Bank mandate",
            bundle_key="joining",
            bundle_label="Joining Documents",
        ),
    ]

    await agent._notify_hr_documents_ready(bundle)

    sent = agent.sent[0]
    assert sent["event_type"] == "onboarding_step_review_ready"
    assert sent["data"]["step_label"] == "Joining Documents"
    assert sent["data"]["document_labels"] == ["Joining kit", "Bank mandate"]


# ── Candidate delivery notification ───────────────────────────────────────


def _delivery(agent, step):
    return agent._delivery_email(
        step,
        dict(RUN_ROW),
        {"name": "Contexo"},
        [{"label": step["label"], "url": "https://signed.example/doc.pdf"}],
        _Settings(),
    )


def test_org_recreated_loi_step_delivers_the_loi_candidate_email(agent):
    step = _step(
        "letter_of_intent",
        document_type_key="letter_of_intent",
        label="Letter of Intent",
    )

    event_type, _ = _delivery(agent, step)

    assert event_type == "onboarding_loi_to_candidate"


def test_org_recreated_induction_step_delivers_the_induction_email(agent):
    step = _step("welcome_pack", document_type_key="induction", label="Welcome pack")

    event_type, _ = _delivery(agent, step)

    assert event_type == "onboarding_induction_ready"


def test_org_composed_step_delivers_the_generic_candidate_email(agent):
    step = _step("offer_letter", document_type_key="offer_letter", label="Offer Letter")

    event_type, data = _delivery(agent, step)

    assert event_type == "onboarding_documents_sent"
    assert data["step_label"] == "Offer Letter"
