"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { CheckCircle2, Cloud, Loader2, Upload, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { openDriveFilePicker } from "@/components/integrations/google-drive-picker";

export interface TemplateStatusRow {
  id: string;
  name: string;
  template_kind: string;
}

export function TemplateSlot({
  kind,
  label,
  current,
  onAssign,
  onDriveImported,
  isBusy,
  driveConnected,
  bare = false,
}: {
  kind: string;
  label: string;
  current: TemplateStatusRow | null;
  onAssign: (docId: string) => void;
  onDriveImported: () => void;
  isBusy: boolean;
  driveConnected: boolean;
  /**
   * When true, renders only the upload/import controls — no card chrome,
   * label, status, or doc name. The templates page supplies its own Actual
   * section card and status pill; the onboarding overview uses the default
   * self-contained card.
   */
  bare?: boolean;
}) {
  const [uploading, setUploading] = useState(false);
  const [uploadLabel, setUploadLabel] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [driveImporting, setDriveImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleDriveImport() {
    setUploadError(null);
    const developerKey = process.env.NEXT_PUBLIC_GOOGLE_API_KEY;
    const appId = process.env.NEXT_PUBLIC_GOOGLE_APP_ID;
    if (!developerKey) {
      setUploadError(
        "Drive picker isn't configured (NEXT_PUBLIC_GOOGLE_API_KEY missing).",
      );
      return;
    }

    setDriveImporting(true);
    try {
      // 1. Refresh access token for the Picker (Drive must be connected by admin).
      const tokenRes = await fetch("/api/integrations/drive/picker-token");
      if (!tokenRes.ok) {
        if (tokenRes.status === 400) {
          setUploadError(
            "Google Drive isn't connected. Connect it in Settings → Integrations.",
          );
        } else if (tokenRes.status === 403) {
          setUploadError("Only admins can import templates from Drive.");
        } else {
          setUploadError("Couldn't authorise the Drive picker — try again.");
        }
        return;
      }
      const { access_token } = (await tokenRes.json()) as { access_token: string };

      // 2. Show Google's native picker (Docs + DOCX only, single select).
      const picked = await openDriveFilePicker({
        accessToken: access_token,
        developerKey,
        appId,
        title: `Select a ${label} template from Drive`,
      });
      if (!picked) return; // user cancelled

      // 3. Hand off to backend to download/export + register as template.
      const importRes = await fetch(
        "/api/onboarding/templates/import-from-drive",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            file_id: picked.id,
            file_name: picked.name,
            mime_type: picked.mimeType,
            template_kind: kind,
          }),
        },
      );
      if (!importRes.ok) {
        const body = (await importRes.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setUploadError(
          body.detail || body.message || "Import from Drive failed.",
        );
        return;
      }
      onDriveImported();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Drive import failed.");
    } finally {
      setDriveImporting(false);
    }
  }

  async function handleFile(file: File) {
    setUploading(true);
    setUploadError(null);
    const contentType = file.type || "application/octet-stream";
    try {
      // 1. Init upload — get presigned URL + doc_id
      setUploadLabel("Uploading…");
      const initRes = await fetch("/api/documents/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          content_type: contentType,
          file_size: file.size,
        }),
      });
      if (!initRes.ok) {
        const b = (await initRes.json().catch(() => ({}))) as { message?: string };
        throw new Error(b.message || "Upload initialisation failed.");
      }
      const { doc_id, upload_url } = (await initRes.json()) as {
        doc_id: string;
        upload_url: string;
      };

      // 2. PUT file directly to storage
      const putRes = await fetch(upload_url, {
        method: "PUT",
        headers: { "Content-Type": contentType },
        body: file,
      });
      if (!putRes.ok) throw new Error("Storage upload failed.");

      // 3. Complete — triggers KB ingestion
      setUploadLabel("Processing…");
      const completeRes = await fetch("/api/documents/upload/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id }),
      });
      if (!completeRes.ok) {
        const b = (await completeRes.json().catch(() => ({}))) as { message?: string };
        throw new Error(b.message || "Processing failed.");
      }

      // 4. Tag as this template kind immediately
      setUploadLabel("Assigning…");
      onAssign(doc_id);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      setUploadLabel(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const controls = (
    <>
      {/* Error */}
      {uploadError ? (
        <p className="mb-2 text-[11px] font-medium text-destructive">{uploadError}</p>
      ) : null}

      {/* Upload button */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) handleFile(f);
          }}
        />
        <Button
          size="sm"
          variant={current ? "outline" : "primary"}
          className="h-8 text-xs"
          disabled={uploading || driveImporting || isBusy}
          onClick={() => {
            setUploadError(null);
            fileRef.current?.click();
          }}
        >
          {uploading ? (
            <>
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
              {uploadLabel}
            </>
          ) : (
            <>
              <Upload className="mr-1.5 h-3 w-3" />
              {current ? "Replace DOCX" : "Upload DOCX"}
            </>
          )}
        </Button>

        {driveConnected ? (
          <Button
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            disabled={uploading || driveImporting || isBusy}
            onClick={handleDriveImport}
            title="Pick a Google Doc or .docx file from your connected Google Drive"
          >
            {driveImporting ? (
              <>
                <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                Importing…
              </>
            ) : (
              <>
                <Cloud className="mr-1.5 h-3 w-3" />
                Import from Drive
              </>
            )}
          </Button>
        ) : (
          <Link
            href="/settings/integrations"
            className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground"
            title="Connect Google Drive to import templates without leaving this page"
          >
            <Cloud className="h-3 w-3" />
            Connect Drive
          </Link>
        )}
      </div>
    </>
  );

  // Bare mode: controls only — the templates page owns the card, label,
  // status pill, and current-doc chip around us.
  if (bare) return controls;

  return (
    <div className="rounded-xl border border-border bg-muted/40 p-3.5">
      {/* Header */}
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-xs font-bold text-foreground">{label}</p>
        {current ? (
          <span className="flex items-center gap-1 text-[10px] font-bold text-success">
            <CheckCircle2 className="h-3 w-3" />
            Configured
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] font-bold text-destructive">
            <XCircle className="h-3 w-3" />
            Not set
          </span>
        )}
      </div>

      {/* Current doc name */}
      {current ? (
        <p className="mb-2 truncate text-[11px] text-muted-foreground">
          {current.name}
        </p>
      ) : null}

      {controls}
    </div>
  );
}
