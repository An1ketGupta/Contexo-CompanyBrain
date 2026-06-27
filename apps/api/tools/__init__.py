"""Standalone dev/CI tools that ship with the API service.

These are NOT loaded by the FastAPI app at runtime — they are run directly
(e.g. `uv run python -m tools.mock_ats_server`) when developing or testing
against external integrations without a real provider account.
"""
