"""Supabase Storage for templates and generated documents.

Two path families, both under the existing `document` bucket so an org delete
cascades them the same way it does the knowledge base:

    orgs/{org_id}/doc-templates/{template_id}/v{version_no}/{filename}
    orgs/{org_id}/generated/{generated_document_id}/g{generation_no}.{docx|pdf}

Both encode a version or generation number in the path, which is what makes
"templates are immutable" and "never overwrite a previous generation" true at
the storage layer rather than only by convention. Uploads use `upsert: false`
so a path collision is an error rather than a silent overwrite — if two
generations ever computed the same path, losing the older artifact is exactly
the failure the schema was designed to prevent.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.database import get_service_client
from app.services.documents.constants import FORMAT_DOCX, MIME_DOCX, MIME_PDF

log = logging.getLogger(__name__)

STORAGE_BUCKET = "document"

# Short by default: a signed URL is handed to a browser that is about to use it,
# and one leaked in a screenshot or a browser-history export should stop working
# quickly.
DEFAULT_SIGNED_URL_TTL = 60 * 60

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class TemplateStorageError(RuntimeError):
    """An upload or download failed. Surfaced to HR as a retryable error."""


def safe_filename(name: str) -> str:
    """Reduce an uploaded filename to something safe for a storage key.

    Keeps the extension readable in the storage browser, which matters when
    someone is trying to work out which object belongs to which template.
    """
    cleaned = _UNSAFE_FILENAME.sub("_", (name or "").strip()) or "template"
    return cleaned[:120]


def template_version_path(
    *, org_id: str, template_id: str, version_no: int, filename: str
) -> str:
    return (
        f"orgs/{org_id}/doc-templates/{template_id}"
        f"/v{version_no}/{safe_filename(filename)}"
    )


def preview_path(*, org_id: str, version_id: str, fmt: str) -> str:
    """Ephemeral render for the template builder.

    Deliberately one path per version rather than per render: a preview is
    disposable and re-rendering should replace it, not accumulate objects. This
    is the only path in the module that is written with overwrite enabled.
    """
    return f"orgs/{org_id}/template-previews/{version_id}.{fmt}"


def generated_file_path(
    *, org_id: str, generated_document_id: str, generation_no: int, fmt: str
) -> str:
    return (
        f"orgs/{org_id}/generated/{generated_document_id}"
        f"/g{generation_no}.{fmt}"
    )


async def upload(
    *,
    path: str,
    data: bytes,
    mime_type: str,
    overwrite: bool = False,
) -> str:
    """Write bytes to Storage. Returns the path written.

    `overwrite` defaults to False on purpose — see the module docstring.
    """
    svc = get_service_client()

    def _put() -> None:
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=path,
            file=data,
            file_options={
                "content-type": mime_type,
                "upsert": "true" if overwrite else "false",
            },
        )

    try:
        await asyncio.to_thread(_put)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.storage_upload_failed path=%s err=%s", path, exc)
        raise TemplateStorageError(
            f"Couldn't save the file to storage: {exc}"
        ) from exc
    return path


async def download(path: str) -> bytes:
    """Read bytes back from Storage."""
    svc = get_service_client()

    def _get() -> bytes:
        return svc.storage.from_(STORAGE_BUCKET).download(path)

    try:
        return await asyncio.to_thread(_get)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.storage_download_failed path=%s err=%s", path, exc)
        raise TemplateStorageError(
            "Couldn't read this file from storage. It may have been removed."
        ) from exc


async def mint_signed_url(path: str, *, ttl_seconds: int = DEFAULT_SIGNED_URL_TTL) -> str | None:
    """Signed download URL, or None on failure.

    Returns None rather than raising so a page listing twenty documents renders
    with a "download unavailable" row instead of failing the whole request.
    """
    svc = get_service_client()

    def _mint() -> str | None:
        res = svc.storage.from_(STORAGE_BUCKET).create_signed_url(
            path=path, expires_in=ttl_seconds
        )
        return res.get("signedURL") or res.get("signed_url")

    try:
        return await asyncio.to_thread(_mint)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.signed_url_failed path=%s err=%s", path, exc)
        return None


async def remove(paths: list[str]) -> None:
    """Delete objects. Best-effort — an orphaned blob is not worth failing a
    delete over, and the row is already gone by the time this runs."""
    if not paths:
        return
    svc = get_service_client()

    def _rm() -> Any:
        return svc.storage.from_(STORAGE_BUCKET).remove(paths)

    try:
        await asyncio.to_thread(_rm)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.storage_remove_failed count=%d err=%s", len(paths), exc)


def mime_for_format(fmt: str) -> str:
    return MIME_DOCX if fmt == FORMAT_DOCX else MIME_PDF
