"""Public surface of the email service.

`send_email_event` is the only entrypoint route handlers should call. It
fires an Inngest event; the actual Resend send happens in a background
worker so a Resend outage never 500s a synchronous request.

Idempotency is enforced by the worker against the email_events table — see
the partial unique indexes in migration 009.
"""
from app.services.email.dispatcher import send_email_event

__all__ = ["send_email_event"]
