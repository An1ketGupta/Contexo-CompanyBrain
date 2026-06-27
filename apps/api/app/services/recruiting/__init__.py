"""Recruiting Agent submodules.

Kept separate from the legacy `services.recruiting_agent` module so the new
production-hardening pieces (mapping resolver, idempotency, audit log,
ATS integration plumbing) live in a clean namespace without disturbing the
existing call sites.
"""
