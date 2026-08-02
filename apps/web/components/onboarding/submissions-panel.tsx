"use client";

import { useState } from "react";
import useSWR from "swr";
import { Check, ExternalLink, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

interface Submission {
  id: string;
  run_step_id: string;
  item_key: string;
  label: string;
  original_filename: string | null;
  file_bytes: number | null;
  submitted_at: string | null;
  review_status: "pending" | "approved" | "rejected";
  review_note: string | null;
  signed_url: string | null;
}

const fetcher = async (url: string): Promise<Submission[]> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

function sizeLabel(bytes: number | null): string {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

/**
 * Every document the candidate has filed on this run, wherever the pipeline
 * has got to.
 *
 * The archive, not the decision. A step waiting on HR shows its own files in
 * `StepApprovalPanel` above, with the accept/send-back that actually moves the
 * run. Marking a file here records a verdict and stops it counting as filed,
 * so it is re-asked the next time its step is; on a step already past its gate
 * that is a note for the record, since nothing re-asks a finished step.
 */
export function SubmissionsPanel({ runId }: { runId: string }) {
  const { data, isLoading, mutate } = useSWR<Submission[]>(
    `/api/onboarding/runs/${runId}/submissions`,
    fetcher,
    { revalidateOnFocus: false },
  );
  const [busy, setBusy] = useState<string | null>(null);

  async function review(
    submission: Submission,
    status: "approved" | "rejected",
  ) {
    const note =
      status === "rejected"
        ? window.prompt(
            `What's wrong with "${submission.label}"? The candidate will see this.`,
            "",
          )
        : null;
    if (status === "rejected" && note === null) return;

    setBusy(submission.id);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${runId}/submissions/${submission.id}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ review_status: status, review_note: note }),
        },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || body?.message || "Couldn't save that.");
      }
      await mutate();
      toast.success(
        status === "approved" ? "Marked as fine." : "Marked for re-upload.",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save that.");
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) {
    return <div className="h-24 animate-pulse rounded-xl bg-muted" />;
  }
  if (!data?.length) return null;

  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-foreground">
          Documents from the candidate
        </h2>
        <p className="text-xs text-muted-foreground">
          Everything filed so far. The step the run is waiting on, if any, is
          reviewed at the top of this page.
        </p>
      </header>

      <div className="space-y-2">
        {data.map((s) => (
          <div
            key={s.id}
            className="flex flex-wrap items-center gap-3 rounded-xl border border-border p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">{s.label}</p>
              <p className="truncate text-xs text-muted-foreground">
                {s.original_filename} {sizeLabel(s.file_bytes)}
                {s.review_note ? ` · ${s.review_note}` : ""}
              </p>
            </div>

            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                s.review_status === "approved"
                  ? "bg-success-tint text-success"
                  : s.review_status === "rejected"
                    ? "bg-destructive-soft text-destructive-ink"
                    : "bg-muted text-muted-foreground",
              )}
            >
              {s.review_status}
            </span>

            {s.signed_url ? (
              <a
                href={s.signed_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 text-xs font-medium text-brand hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Open
              </a>
            ) : null}

            {s.review_status !== "approved" ? (
              <button
                type="button"
                disabled={busy === s.id}
                onClick={() => review(s, "approved")}
                aria-label={`Approve ${s.label}`}
                className="rounded-lg border border-border p-1.5 text-success hover:border-success disabled:opacity-50"
              >
                {busy === s.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
              </button>
            ) : null}

            {s.review_status !== "rejected" ? (
              <button
                type="button"
                disabled={busy === s.id}
                onClick={() => review(s, "rejected")}
                aria-label={`Send back ${s.label}`}
                className="rounded-lg border border-border p-1.5 text-destructive-ink hover:border-destructive disabled:opacity-50"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
