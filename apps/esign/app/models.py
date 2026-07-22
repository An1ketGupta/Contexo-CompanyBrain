from __future__ import annotations

from pydantic import BaseModel, EmailStr


class SignerIn(BaseModel):
    role: str  # our internal role, e.g. "hr" | "candidate" — mapped to a Documenso SIGNER
    email: EmailStr
    name: str
    routing_order: int = 1


class CreateEnvelopeRequest(BaseModel):
    org_id: str
    run_id: str
    document_kinds: list[str]
    # Path (in the shared "document" Supabase Storage bucket) to the single,
    # already-rendered PDF to sign. If a document_kind list has more than one
    # entry (e.g. appointment_letter + nda), the caller is responsible for
    # merging them into one PDF before calling — this service always signs
    # exactly one PDF per envelope.
    storage_path: str
    signers: list[SignerIn]
    # Inngest event name to fire when every signer has completed — e.g.
    # "onboarding_v2/loi_signed_uploaded". This is what re-kicks the caller's
    # own agent; this service doesn't know or care what it means.
    completion_event: str


class SignerOut(BaseModel):
    role: str
    email: str
    name: str
    signing_url: str


class CreateEnvelopeResponse(BaseModel):
    envelope_id: str
    signers: list[SignerOut]


class EnvelopeStatus(BaseModel):
    envelope_id: str
    status: str
    signers: list[dict]


class SignPrefill(BaseModel):
    """What the /sign/{token} page needs to embed Documenso's signer.

    The signing UI is Documenso's, rendered by @documenso/embed-react in an
    iframe against `documenso_host` with `documenso_token`. NirnayaIQ no longer
    collects the signature itself — hence no preview_url / submit models here.
    """
    envelope_id: str
    role: str
    signer_name: str
    document_kinds: list[str]
    documenso_host: str  # public Documenso URL the iframe loads from
    documenso_token: str  # recipient signing token for the embed
    already_signed: bool
