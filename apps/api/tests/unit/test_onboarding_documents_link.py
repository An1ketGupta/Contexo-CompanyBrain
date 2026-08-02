"""The candidate's document upload link.

A `collect` step used to email a link into the dashboard, which meant the
candidate had to find the magic-link email sent weeks earlier at run creation.
The link now carries its own credential, the same way the reference forms do.

Two things are worth pinning down. The token has to survive being asked twice —
a run with a second document step must not hand out a new URL that orphans the
first one — and every gate the signed-in path enforces has to hold on the link
path too, since it is the same checklist reachable without a session.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

import app.inngest  # noqa: F401  (import-order cycle; see test_onboarding_step_toggles)
from app.models.onboarding_catalog import CandidateOnboardingRead, CandidateStepRead
from app.routers import onboarding_catalog as catalog_router
from app.routers import onboarding_public as public_router
from app.services.agents.onboarding_v2 import agent as agent_mod
from app.services.agents.onboarding_v2.agent import OnboardingV2Agent
from tests.unit.fake_supabase import FakeSupabase

ORG = "org-1"
RUN = "run-1"
TOKEN = "11111111-1111-4111-8111-111111111111"


def _run_row(**overrides):
    row = {
        "id": RUN,
        "org_id": ORG,
        "status": "awaiting_candidate_documents",
        "candidate_name": "Priya Nair",
        "candidate_email": "priya@example.com",
        "role_title": "Product Designer",
        "pre_join_user_id": "user-candidate",
        "documents_token": TOKEN,
        "documents_token_expires_at": (
            datetime.now(UTC) + timedelta(days=30)
        ).isoformat(),
    }
    row.update(overrides)
    return row


@pytest.fixture
def db(monkeypatch):
    """A store both the public router and the agent read the run out of."""
    store = FakeSupabase({"onboarding_runs": [_run_row()]})
    monkeypatch.setattr(public_router, "get_service_client", lambda: store)
    monkeypatch.setattr(agent_mod, "get_service_client", lambda: store)
    return store


@pytest.fixture
def view(monkeypatch):
    """The shared checklist builder, stubbed to a known answer."""
    calls: list[dict] = []

    async def fake_build(run):
        calls.append(run)
        return CandidateOnboardingRead(
            run_id=run["id"],
            candidate_name=run["candidate_name"],
            company_name="Contexo",
            role_title=run["role_title"],
            steps=[
                CandidateStepRead(
                    step_key="joining_documents",
                    label="Joining documents",
                    status="active",
                )
            ],
        )

    monkeypatch.setattr(catalog_router, "build_candidate_view", fake_build)
    return calls


# ── Opening the link ──────────────────────────────────────────────────────


async def test_link_serves_the_same_checklist_as_the_portal(db, view):
    out = await public_router.get_candidate_documents(TOKEN)

    assert out.candidate_name == "Priya Nair"
    assert [s.step_key for s in out.steps] == ["joining_documents"]
    assert out.expires_at is not None
    # The run row itself is handed to the shared builder — the link path adds
    # no view of its own that could drift from the signed-in one.
    assert view[0]["id"] == RUN


async def test_unknown_token_is_not_found(db, view):
    with pytest.raises(HTTPException) as exc:
        await public_router.get_candidate_documents(str(uuid.uuid4()))
    assert exc.value.status_code == 404


async def test_malformed_token_is_rejected_before_any_lookup(db, view):
    with pytest.raises(HTTPException) as exc:
        await public_router.get_candidate_documents("not-a-uuid")
    assert exc.value.status_code == 400


async def test_expired_link_is_gone(db, view):
    db.tables["onboarding_runs"][0]["documents_token_expires_at"] = (
        datetime.now(UTC) - timedelta(days=1)
    ).isoformat()

    with pytest.raises(HTTPException) as exc:
        await public_router.get_candidate_documents(TOKEN)
    assert exc.value.status_code == 410


async def test_cancelled_run_stops_accepting_documents(db, view):
    db.tables["onboarding_runs"][0]["status"] = "cancelled"

    with pytest.raises(HTTPException) as exc:
        await public_router.get_candidate_documents(TOKEN)
    assert exc.value.status_code == 410


# ── Uploading through the link ────────────────────────────────────────────


@pytest.fixture
def upload(monkeypatch):
    calls: list[dict] = []

    async def fake_store(*, run, step_key, item_key, file, submitted_by):
        calls.append(
            {
                "run_id": run["id"],
                "step_key": step_key,
                "item_key": item_key,
                "submitted_by": submitted_by,
            }
        )
        return {"ok": True, "item_key": item_key, "step_complete": False}

    monkeypatch.setattr(catalog_router, "store_candidate_upload", fake_store)
    return calls


async def test_upload_goes_through_the_shared_writer(db, upload):
    out = await public_router.upload_candidate_document_public(
        TOKEN, "joining_documents", "pan_card", file=object()
    )

    assert out["ok"] is True
    assert upload == [
        {
            "run_id": RUN,
            "step_key": "joining_documents",
            "item_key": "pan_card",
            # Nobody signed in, so there is no user to attribute it to. The
            # column is provenance, not the authorisation.
            "submitted_by": None,
        }
    ]


async def test_expired_link_cannot_still_be_uploaded_against(db, upload):
    """A tab left open past the expiry is not a way back in."""
    db.tables["onboarding_runs"][0]["documents_token_expires_at"] = (
        datetime.now(UTC) - timedelta(days=1)
    ).isoformat()

    with pytest.raises(HTTPException) as exc:
        await public_router.upload_candidate_document_public(
            TOKEN, "joining_documents", "pan_card", file=object()
        )
    assert exc.value.status_code == 410
    assert upload == []


# ── Minting the link ──────────────────────────────────────────────────────


class _Settings:
    app_url = "https://app.example.com"


@pytest.fixture
def agent(monkeypatch, db):
    a = OnboardingV2Agent(org_id=ORG, run_id=RUN)
    a.sent: list[dict] = []

    async def fake_send_email_event(**kwargs):
        a.sent.append(kwargs)

    async def fake_branding():
        return {"name": "Contexo"}

    async def fake_log_event(**kwargs):
        return None

    monkeypatch.setattr(agent_mod, "send_email_event", fake_send_email_event)
    monkeypatch.setattr(agent_mod, "get_settings", lambda: _Settings())
    monkeypatch.setattr(agent_mod.ob_storage, "log_onboarding_event", fake_log_event)
    monkeypatch.setattr(a, "_resolve_org_branding", fake_branding)
    return a


def _collect_step():
    return {
        "id": "rs-joining",
        "step_key": "joining_documents",
        "label": "Joining documents",
        "config": {
            "items": [
                {"item_key": "pan_card", "label": "PAN card", "required": True},
                {"item_key": "photo", "label": "Photo", "required": False},
            ]
        },
    }


async def test_ask_links_to_the_public_page_not_the_dashboard(agent, db):
    await agent._notify_candidate_documents_due(_collect_step())

    data = agent.sent[0]["data"]
    assert data["portal_url"] == f"https://app.example.com/candidate/documents/{TOKEN}"
    # Only required items are counted — an optional one is not something the
    # candidate is being told they must produce.
    assert data["document_count"] == 1
    assert data["document_labels"] == ["PAN card"]


async def test_a_run_without_a_token_yet_gets_one(agent, db):
    db.tables["onboarding_runs"][0]["documents_token"] = None
    db.tables["onboarding_runs"][0]["documents_token_expires_at"] = None

    await agent._notify_candidate_documents_due(_collect_step())

    minted = db.tables["onboarding_runs"][0]["documents_token"]
    assert minted and minted != TOKEN
    assert uuid.UUID(minted).version == 4
    assert agent.sent[0]["data"]["portal_url"].endswith(f"/candidate/documents/{minted}")


async def test_a_second_ask_reuses_the_link_and_extends_it(agent, db):
    lapsing = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    db.tables["onboarding_runs"][0]["documents_token_expires_at"] = lapsing

    await agent._notify_candidate_documents_due(_collect_step())

    row = db.tables["onboarding_runs"][0]
    # Same URL — a candidate who bookmarked the first ask finds the second one
    # there — but good for another 30 days rather than expiring tomorrow.
    assert row["documents_token"] == TOKEN
    assert row["documents_token_expires_at"] > lapsing
