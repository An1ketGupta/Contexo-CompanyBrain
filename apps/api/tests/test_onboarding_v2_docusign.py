"""Tests for the DocuSign webhook signature verification + payload mapping.

Network-touching paths (create_signing_envelope, get_signing_url) require
real DocuSign credentials and are excluded from the default test run via
the `integration` marker.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import pytest


def _hmac_b64(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


def test_verify_webhook_signature_accepts_correct_hmac(monkeypatch) -> None:
    from app.services.integrations.docusign import client as docusign_client
    from app.services.integrations.docusign import verify_webhook_signature

    secret = "test-webhook-key"
    body = b'{"envelopeId": "abc", "status": "completed"}'
    sig = _hmac_b64(secret, body)

    monkeypatch.setattr(
        docusign_client,
        "_config",
        lambda: {
            "webhook_hmac_key": secret,
            "integration_key": "k",
            "user_id": "u",
            "account_id": "a",
            "base_url": "https://example.com",
            "rsa_private_key": "key",
            "auth_server": "account-d.docusign.com",
        },
    )
    assert verify_webhook_signature(body=body, signature_header=sig) is True


def test_verify_webhook_signature_rejects_wrong_secret(monkeypatch) -> None:
    from app.services.integrations.docusign import client as docusign_client
    from app.services.integrations.docusign import verify_webhook_signature

    body = b'{"envelopeId": "abc"}'
    monkeypatch.setattr(
        docusign_client, "_config",
        lambda: {"webhook_hmac_key": "real-key"},
    )
    sig = _hmac_b64("wrong-key", body)
    assert verify_webhook_signature(body=body, signature_header=sig) is False


def test_verify_webhook_signature_rejects_missing_header(monkeypatch) -> None:
    from app.services.integrations.docusign import client as docusign_client
    from app.services.integrations.docusign import verify_webhook_signature

    monkeypatch.setattr(
        docusign_client, "_config",
        lambda: {"webhook_hmac_key": "real-key"},
    )
    assert verify_webhook_signature(body=b'{}', signature_header=None) is False


def test_verify_webhook_signature_rejects_when_secret_unset(monkeypatch) -> None:
    """If DOCUSIGN_WEBHOOK_HMAC_KEY isn't configured we MUST refuse to
    accept any signature — silent acceptance would create an open relay."""
    from app.services.integrations.docusign import client as docusign_client
    from app.services.integrations.docusign import verify_webhook_signature

    monkeypatch.setattr(docusign_client, "_config", lambda: {"webhook_hmac_key": ""})
    body = b'{}'
    assert verify_webhook_signature(body=body, signature_header="anything") is False
