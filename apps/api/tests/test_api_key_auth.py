"""Tests for the API-key auth dependency.

Covers the credential-enumeration fix: 'unknown' and 'revoked' keys
must produce the same 401 body and headers. The discriminator value
is logged server-side only.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.routers.public_api import get_api_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verify_error",
    [
        "missing_bearer",
        "malformed_key",
        "unknown_key",
        "revoked_key",
    ],
)
async def test_every_auth_failure_returns_identical_401(verify_error: str) -> None:
    """All four verify_key failure modes must collapse to the same 401 body
    so a caller can't distinguish 'this key existed and was revoked' from
    'this key was never issued'."""

    async def _raise(_auth):
        raise ValueError(verify_error)

    with patch("app.routers.public_api.verify_key", _raise):
        with pytest.raises(HTTPException) as excinfo:
            await get_api_context(authorization="Bearer ck_live_nope")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid API credentials."
    assert excinfo.value.headers == {"WWW-Authenticate": "Bearer"}
