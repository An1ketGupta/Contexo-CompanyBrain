"""DocuSign integration — embedded-signing for onboarding offer bundles.

Surface used by the Onboarding v2 agent + the public webhook handler:

  * `create_signing_envelope(run, documents)` — wrap one-or-more PDFs as an
    envelope, return the embedded-signing URL + envelope_id. We use the
    embedded flow (Signing Ceremony URL) over the email-flow because it
    lets us redirect back to our own thank-you page and gives us a
    one-click "sign now" experience inside our own UI.

  * `void_envelopes_for_run(run_id, reason)` — called on cancel to retract
    any open envelope.

  * `ingest_webhook_event(envelope_id, body)` — invoked by the
    /docusign/webhook router after HMAC verification. Updates
    onboarding_signing_envelopes + flips the linked onboarding_documents
    sign_status fields.

Configuration: this module reads four env vars (all required only when an
org has the docusign integration enabled — module loads without them):

  DOCUSIGN_INTEGRATION_KEY  — Integration Key (client_id) for the app
  DOCUSIGN_USER_ID          — Impersonation user GUID (JWT grant flow)
  DOCUSIGN_ACCOUNT_ID       — Account GUID
  DOCUSIGN_BASE_URL         — https://demo.docusign.net/restapi (sandbox)
                              or https://www.docusign.net/restapi (prod)
  DOCUSIGN_RSA_PRIVATE_KEY  — RSA private key (PEM, multi-line ok) used to
                              sign the JWT assertion
  DOCUSIGN_WEBHOOK_HMAC_KEY — Secret for verifying inbound webhook signatures

We deliberately keep the SDK out of this module — the official DocuSign
Python SDK is heavy and locks in transitively. The two REST calls we need
(create envelope, void envelope) are small enough to do with httpx +
the JWT grant flow. If the surface grows we can revisit.
"""
from app.services.integrations.docusign.client import (  # noqa: F401
    DocuSignClient,
    DocuSignError,
    DocuSignUnavailable,
    create_routed_signing_envelope,
    create_signing_envelope,
    get_signing_url,
    ingest_webhook_event,
    mint_signer_url,
    verify_webhook_signature,
    void_envelopes_for_run,
)

__all__ = [
    "DocuSignClient",
    "DocuSignError",
    "DocuSignUnavailable",
    "create_routed_signing_envelope",
    "create_signing_envelope",
    "get_signing_url",
    "ingest_webhook_event",
    "mint_signer_url",
    "verify_webhook_signature",
    "void_envelopes_for_run",
]
