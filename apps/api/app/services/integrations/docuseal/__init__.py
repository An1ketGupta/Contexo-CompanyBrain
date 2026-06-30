"""DocuSeal integration — embedded-signing for the onboarding LOI flow.

We self-host DocuSeal (Fly.io, sign.nirnayaiq.com) as a cheap, open-source
replacement for DocuSign. The public surface mirrors what the DocuSign
adapter exposed so call-sites change only the import path.

Surface used by the Onboarding v2 agent + the public webhook handler:

  * `create_signing_envelope(...)` — wrap one PDF as a submission, return
    the embedded-signing URL (`embed_src`) + submission_id. We persist the
    envelope row with provider='docuseal'.

  * `create_routed_signing_envelope(...)` — multi-submitter routed
    envelope (HR signs first, candidate signs second). HR submitter has
    `send_email=false` (link surfaced via our UI); candidate is
    `send_email=true` so DocuSeal mails them after HR completes.

  * `mint_signer_url(...)` — return the stored embed_src for a signer.
    Unlike DocuSign, DocuSeal slugs don't expire so this is a pure DB
    read, no API call.

  * `void_envelopes_for_run(...)` — DELETE /submissions/{id} for each
    active envelope. Used by run cancellation.

  * `ingest_webhook_event(body)` — process a DocuSeal webhook payload
    (caller already verified HMAC). Updates the envelope/document rows,
    downloads the signed PDF, kicks the agent forward on completion.

Configuration: three env vars (all required only when an org has the
integration enabled; module loads without them):

  DOCUSEAL_BASE_URL       — https://sign.nirnayaiq.com (no trailing slash)
  DOCUSEAL_API_KEY        — X-Auth-Token header value; rotate via DocuSeal
                            admin → API
  DOCUSEAL_WEBHOOK_SECRET — Shared secret matching the DocuSeal container's
                            WEBHOOK_SECRET env var

We deliberately keep the SDK out of this module — DocuSeal's API is small
enough that httpx + a static API key is simpler than pulling in an SDK.
"""
from app.services.integrations.docuseal.client import (  # noqa: F401
    DocuSealClient,
    DocuSealError,
    DocuSealUnavailable,
    create_routed_signing_envelope,
    create_signing_envelope,
    get_signing_url,
    ingest_webhook_event,
    mint_signer_url,
    verify_webhook_signature,
    void_envelopes_for_run,
)

__all__ = [
    "DocuSealClient",
    "DocuSealError",
    "DocuSealUnavailable",
    "create_routed_signing_envelope",
    "create_signing_envelope",
    "get_signing_url",
    "ingest_webhook_event",
    "mint_signer_url",
    "verify_webhook_signature",
    "void_envelopes_for_run",
]
