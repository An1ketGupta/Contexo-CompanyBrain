"use client";

import { useRef, useState } from "react";
import { Check, FileText, Loader2, RotateCcw, Upload } from "lucide-react";

import { cn } from "@/lib/utils";

export interface CandidateItem {
  item_key: string;
  label: string;
  help_text: string | null;
  required: boolean;
  accepted_formats: string[];
  submitted: boolean;
  original_filename: string | null;
  submitted_at: string | null;
  review_status: string | null;
  review_note: string | null;
}

/**
 * One checklist row with its own drop target.
 *
 * Deliberately not built on the dashboard's `useUploads()` queue: that context
 * is wired to the internal knowledge-base ingest API, and these files are
 * candidate PII that must never be parsed, chunked or embedded. A plain
 * multipart POST per file is the whole requirement.
 */
export function CollectUploader({
  stepKey,
  item,
  onUploaded,
}: {
  stepKey: string;
  item: CandidateItem;
  onUploaded: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = item.accepted_formats.map((f) => `.${f}`).join(",");
  const rejected = item.review_status === "rejected";

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `/api/onboarding/candidate/steps/${encodeURIComponent(stepKey)}` +
          `/items/${encodeURIComponent(item.item_key)}/upload`,
        { method: "POST", body: form },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(
          body?.detail || body?.message || "That upload didn't go through.",
        );
      }
      onUploaded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "That upload didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  function highlight(on: boolean) {
    dropRef.current?.classList.toggle("border-brand", on);
    dropRef.current?.classList.toggle("bg-brand-tint", on);
  }

  return (
    <div
      ref={dropRef}
      onDragOver={(e) => {
        e.preventDefault();
        highlight(true);
      }}
      onDragLeave={() => highlight(false)}
      onDrop={(e) => {
        e.preventDefault();
        highlight(false);
        const file = e.dataTransfer.files?.[0];
        if (file) upload(file);
      }}
      className={cn(
        "rounded-xl border p-4 transition-colors",
        item.submitted && !rejected
          ? "border-success bg-success-tint/30"
          : rejected
            ? "border-destructive bg-destructive-soft/30"
            : "border-dashed border-border bg-card",
      )}
    >
      <div className="flex flex-wrap items-center gap-3">
        <span
          aria-hidden
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
            item.submitted && !rejected
              ? "bg-success text-white"
              : "bg-muted text-muted-foreground",
          )}
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : item.submitted && !rejected ? (
            <Check className="h-4 w-4" />
          ) : (
            <FileText className="h-4 w-4" />
          )}
        </span>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">
            {item.label}
            {item.required ? null : (
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                Optional
              </span>
            )}
          </p>
          <p className="text-xs text-muted-foreground">
            {rejected
              ? (item.review_note ?? "This needs to be re-uploaded.")
              : item.submitted
                ? (item.original_filename ?? "Uploaded")
                : (item.help_text ??
                  `${item.accepted_formats.join(", ").toUpperCase()} · drag and drop or browse`)}
          </p>
          {error ? (
            <p className="mt-1 text-xs text-destructive-ink">{error}</p>
          ) : null}
        </div>

        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          className={cn(
            "flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium transition-colors",
            "hover:border-brand hover:text-brand disabled:opacity-50",
          )}
        >
          {item.submitted ? (
            <>
              <RotateCcw className="h-3.5 w-3.5" />
              Replace
            </>
          ) : (
            <>
              <Upload className="h-3.5 w-3.5" />
              Upload
            </>
          )}
        </button>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={accept}
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) upload(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
