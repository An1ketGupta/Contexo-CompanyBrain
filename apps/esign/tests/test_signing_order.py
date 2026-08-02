"""The envelope must be created SEQUENTIAL, or routing order means nothing.

A recipient's numeric `signingOrder` is only enforced when the envelope itself
is in SEQUENTIAL mode. Documenso's default is PARALLEL, under which every
recipient becomes signable — and, with the default EMAIL distribution, is
mailed — the moment the envelope is distributed. We were sending the numbers
without the mode, so a step configured "HR signs, then the candidate" let the
candidate go first and the candidate's mail arrived alongside (or ahead of)
HR's.

`meta.signingOrder` is settable on create only — the distribute endpoint takes
`meta.distributionMethod` but not `meta.signingOrder` — so create is the one
place this can be established.
"""
from __future__ import annotations

import json

import pytest

from app import documenso_client as documenso
from app.field_placement import SignatureField

CFG = {"base_url": "http://documenso.test", "token": "tok"}

RECIPIENTS = [
    documenso.RecipientSpec(role="hr", email="hr@acme.test", name="HR", signing_order=1),
    documenso.RecipientSpec(
        role="candidate", email="cand@acme.test", name="Cand", signing_order=2
    ),
]

FIELDS = [
    SignatureField(role="hr", page=1, position_x=10, position_y=10, width=20, height=5),
    SignatureField(
        role="candidate", page=2, position_x=10, position_y=10, width=20, height=5
    ),
]


@pytest.fixture
def calls(monkeypatch):
    """Record every Documenso request and answer it with a plausible shape."""
    seen: list[dict] = []

    async def fake_request(method, path, *, cfg, json_body=None, data=None, files=None):
        seen.append(
            {"method": method, "path": path, "json": json_body, "data": data}
        )
        if path.endswith("/envelope/create"):
            return {"id": "env_1"}
        if path.startswith("/api/v2/envelope/env_1"):
            return {
                "id": "env_1",
                "envelopeItems": [{"id": "item_1"}],
                "recipients": [
                    {"id": 11, "email": "hr@acme.test", "signingOrder": 1, "token": "t1"},
                    {
                        "id": 12,
                        "email": "cand@acme.test",
                        "signingOrder": 2,
                        "token": "t2",
                    },
                ],
            }
        if path.endswith("/envelope/distribute"):
            return {"recipients": [{"id": 11, "token": "t1"}, {"id": 12, "token": "t2"}]}
        return {}

    monkeypatch.setattr(documenso, "_require_configured", lambda: CFG)
    monkeypatch.setattr(documenso, "_request", fake_request)
    return seen


def _create_payload(calls: list[dict]) -> dict:
    create = next(c for c in calls if c["path"].endswith("/envelope/create"))
    return json.loads(create["data"]["payload"])


@pytest.mark.asyncio
async def test_envelope_is_created_sequential(calls):
    await documenso.create_and_distribute(
        title="LOI",
        pdf_bytes=b"%PDF-1.4",
        pdf_filename="run.pdf",
        recipients=RECIPIENTS,
        fields=FIELDS,
    )

    assert _create_payload(calls)["meta"]["signingOrder"] == "SEQUENTIAL"


@pytest.mark.asyncio
async def test_create_suppresses_documenso_emails(calls):
    """Distribution defaults to EMAIL and is stored at create, so a create that
    omits it mails every recipient before Contexo gets to route anyone."""
    await documenso.create_and_distribute(
        title="LOI",
        pdf_bytes=b"%PDF-1.4",
        pdf_filename="run.pdf",
        recipients=RECIPIENTS,
        fields=FIELDS,
    )

    assert _create_payload(calls)["meta"]["distributionMethod"] == "NONE"


@pytest.mark.asyncio
async def test_recipient_routing_numbers_are_preserved(calls):
    await documenso.create_and_distribute(
        title="LOI",
        pdf_bytes=b"%PDF-1.4",
        pdf_filename="run.pdf",
        recipients=RECIPIENTS,
        fields=FIELDS,
    )

    ordered = [
        (r["email"], r["signingOrder"]) for r in _create_payload(calls)["recipients"]
    ]
    assert ordered == [("hr@acme.test", 1), ("cand@acme.test", 2)]


@pytest.mark.asyncio
async def test_distribute_still_asks_for_no_email(calls):
    await documenso.create_and_distribute(
        title="LOI",
        pdf_bytes=b"%PDF-1.4",
        pdf_filename="run.pdf",
        recipients=RECIPIENTS,
        fields=FIELDS,
    )

    distribute = next(c for c in calls if c["path"].endswith("/envelope/distribute"))
    assert distribute["json"]["meta"]["distributionMethod"] == "NONE"
