"""Regression tests for an envelope whose signers share one email address.

The bug: HR and the candidate are the same address often enough in practice
(a founder onboarding themselves, anyone exercising the flow end to end).
Documenso creates two recipients for it, but we resolved role → recipient id
by email, so both roles pointed at the first recipient. Every signature field
piled onto recipient #1 and recipient #2 got none, so `distribute` failed with

    MISSING_SIGNATURE_FIELD — "Signers must have at least one signature field"

and the whole envelope died at "Couldn't create the signing envelope".

The same email-as-key mistake sat in completion correlation: one signature
marked both signers done, so the run skipped the second signature entirely.
"""
from __future__ import annotations

import pytest

from app import documenso_client as documenso
from app.field_placement import SignatureField
from app.routers import webhooks

SHARED_EMAIL = "guptaaniket600.ag@gmail.com"

# Shape mirrors GET /api/v2/envelope/{id} — HR at order 1, candidate at 2.
REMOTE_RECIPIENTS = [
    {"id": 35, "email": SHARED_EMAIL, "name": "Aniket Gupta", "signingOrder": 1},
    {"id": 36, "email": SHARED_EMAIL, "name": "Emma Johnson", "signingOrder": 2},
]


def _resolve(specs: list[tuple[str, str, int]]) -> dict[str, int]:
    role_to_id: dict[str, int] = {}
    claimed: set[int] = set()
    for role, email, order in specs:
        rid = documenso._match_recipient_id(
            REMOTE_RECIPIENTS, email=email, signing_order=order, claimed=claimed
        )
        role_to_id[role] = rid
        claimed.add(rid)
    return role_to_id


def test_shared_email_signers_get_distinct_recipient_ids():
    role_to_id = _resolve([("hr", SHARED_EMAIL, 1), ("candidate", SHARED_EMAIL, 2)])
    assert role_to_id == {"hr": 35, "candidate": 36}


def test_shared_email_resolves_by_signing_order_regardless_of_input_order():
    role_to_id = _resolve([("candidate", SHARED_EMAIL, 2), ("hr", SHARED_EMAIL, 1)])
    assert role_to_id == {"hr": 35, "candidate": 36}


def test_distinct_emails_still_match_by_email():
    recipients = [
        {"id": 35, "email": "hr@acme.test", "signingOrder": 1},
        {"id": 36, "email": "candidate@acme.test", "signingOrder": 2},
    ]
    assert (
        documenso._match_recipient_id(
            recipients, email="candidate@acme.test", signing_order=2, claimed=set()
        )
        == 36
    )


def test_unknown_email_raises():
    with pytest.raises(documenso.DocumensoError):
        documenso._match_recipient_id(
            REMOTE_RECIPIENTS, email="nobody@acme.test", signing_order=1, claimed=set()
        )


def test_signing_order_mismatch_falls_back_to_any_unclaimed_same_email():
    """Documenso may not echo signingOrder back. Distinct ids still matter more
    than exact ordering, so each role must still get its own recipient."""
    recipients = [
        {"id": 35, "email": SHARED_EMAIL},
        {"id": 36, "email": SHARED_EMAIL},
    ]
    claimed: set[int] = set()
    first = documenso._match_recipient_id(
        recipients, email=SHARED_EMAIL, signing_order=1, claimed=claimed
    )
    claimed.add(first)
    second = documenso._match_recipient_id(
        recipients, email=SHARED_EMAIL, signing_order=2, claimed=claimed
    )
    assert {first, second} == {35, 36}


def test_every_role_receives_a_field_when_ids_are_distinct():
    """The field list is built per role; with a 1:1 role→recipient map both
    Documenso recipients end up with a SIGNATURE field."""
    role_to_id = _resolve([("hr", SHARED_EMAIL, 1), ("candidate", SHARED_EMAIL, 2)])
    fields = [
        SignatureField(role="hr", page=2, position_x=29.3, position_y=10.4, width=29.4, height=6.0),
        SignatureField(role="candidate", page=4, position_x=25.2, position_y=58.1, width=29.4, height=6.0),
    ]
    bound = {role_to_id[f.role] for f in fields}
    assert bound == {35, 36}


def test_one_signature_does_not_complete_the_other_shared_email_signer():
    signers = [
        {"role": "hr", "email": SHARED_EMAIL, "documenso_recipient_id": 35, "status": "pending"},
        {"role": "candidate", "email": SHARED_EMAIL, "documenso_recipient_id": 36, "status": "pending"},
    ]
    changed = webhooks.mark_done_signers(
        signers,
        [
            {"id": 35, "email": SHARED_EMAIL, "signingStatus": "SIGNED"},
            {"id": 36, "email": SHARED_EMAIL, "signingStatus": "NOT_SIGNED"},
        ],
        now_iso="2026-07-30T00:00:00+00:00",
    )
    assert changed is True
    assert signers[0]["status"] == "completed"
    assert signers[1]["status"] == "pending"


def test_completion_falls_back_to_email_without_a_stored_recipient_id():
    signers = [{"role": "hr", "email": "hr@acme.test", "status": "pending"}]
    changed = webhooks.mark_done_signers(
        signers,
        [{"id": 35, "email": "hr@acme.test", "signingStatus": "SIGNED"}],
        now_iso="2026-07-30T00:00:00+00:00",
    )
    assert changed is True
    assert signers[0]["status"] == "completed"


def test_no_change_when_nobody_signed():
    signers = [
        {"role": "hr", "email": SHARED_EMAIL, "documenso_recipient_id": 35, "status": "pending"},
    ]
    assert (
        webhooks.mark_done_signers(
            signers,
            [{"id": 35, "email": SHARED_EMAIL, "signingStatus": "NOT_SIGNED"}],
            now_iso="2026-07-30T00:00:00+00:00",
        )
        is False
    )
    assert signers[0]["status"] == "pending"
