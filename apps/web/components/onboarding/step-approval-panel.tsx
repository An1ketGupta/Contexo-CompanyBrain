"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  Loader2,
  RotateCcw,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { RunStep } from "@/components/onboarding/step-panel";
import { cn } from "@/lib/utils";

/** The bits of a run document this panel shows. Structural so the run page
 *  can pass its own row type without a cast. */
export interface ApprovalDocument {
  kind: string;
  signed_url: string | null;
  sign_status: string;
  signed_uploaded_at: string | null;
  esign_signers: { role: string; name: string; status: string }[];
}

export interface ApprovalReference {
  id: string;
  reference_name: string;
  reference_email: string;
  relationship: string | null;
  status: string;
}

interface Submission {
  id: string;
  run_step_id: string;
  item_key: string;
  label: string;
  original_filename: string | null;
  review_status: "pending" | "approved" | "rejected";
  review_note: string | null;
  signed_url: string | null;
}

const fetcher = async (url: string): Promise<Submission[]> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

/**
 * The gate. Whatever the candidate just did, HR sees it here and either accepts
 * it — the run carries on — or sends it back with a note, which re-asks that
 * one step without unwinding anything before it.
 *
 * Rendered for any step sitting in `pending_hr_approval`, built-in or not. The
 * panels below it are per-document-type and only exist for the five steps the
 * product ships; a step an org composed itself reaches this gate too, and
 * showing it nothing to act on was the whole gap.
 */
export function StepApprovalPanel({
  runId,
  steps,
  documents,
  references,
  onReviewed,
}: {
  runId: string;
  /** The step in the gate, plus anything bundled with it. */
  steps: RunStep[];
  documents: ApprovalDocument[];
  references: ApprovalReference[];
  onReviewed: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState<"approved" | "rejected" | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const lead = steps[0];
  const { data: submissions, mutate: mutateSubmissions } = useSWR<Submission[]>(
    lead.kind === "collect"
      ? `/api/onboarding/runs/${runId}/submissions`
      : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  const label = lead.bundle_label ?? lead.label;
  const resent = lead.approval_round > 0;

  async function review(decision: "approved" | "rejected") {
    if (decision === "rejected" && !note.trim()) {
      setError("Say what needs redoing — it's all the candidate is told.");
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${runId}/steps/${encodeURIComponent(lead.step_key)}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, note: note.trim() || null }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as {
          detail?: string;
          message?: string;
        } | null;
        throw new Error(
          body?.detail || body?.message || "Couldn't save that decision.",
        );
      }
      setNote("");
      setRejecting(false);
      await onReviewed();
      toast.success(
        decision === "approved"
          ? `${label} accepted — onboarding continues.`
          : `Sent back. ${lead.kind === "generate" ? "A fresh signing request is on its way." : "The candidate has been asked to redo it."}`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save that decision.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4 rounded-2xl border border-brand/40 bg-brand-tint/30 p-4">
      <div>
        <p className="text-sm font-semibold text-foreground">
          {resent
            ? `Check ${label} again (attempt ${lead.approval_round + 1})`
            : `Check ${label} before onboarding continues`}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {GATE_BLURB[lead.kind]}
        </p>
      </div>

      {lead.kind === "generate" ? (
        <SignedDocuments steps={steps} documents={documents} />
      ) : null}

      {lead.kind === "collect" ? (
        <SubmittedFiles
          stepId={lead.id}
          runId={runId}
          submissions={submissions}
          onChanged={mutateSubmissions}
        />
      ) : null}

      {lead.kind === "system" ? <NamedReferences references={references} /> : null}

      {rejecting ? (
        <div className="space-y-2">
          <label
            htmlFor="reject-note"
            className="block text-xs font-medium text-foreground"
          >
            What needs redoing? The candidate sees this word for word.
          </label>
          <Textarea
            id="reject-note"
            rows={3}
            value={note}
            autoFocus
            onChange={(e) => setNote(e.target.value)}
            placeholder={REJECT_PLACEHOLDER[lead.kind]}
          />
        </div>
      ) : null}

      {error ? (
        <p className="flex items-start gap-1.5 text-xs font-medium text-destructive">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {rejecting ? (
          <>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => review("rejected")}
              disabled={busy !== null}
            >
              {busy === "rejected" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              )}
              {REJECT_CONFIRM[lead.kind]}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setRejecting(false);
                setError(null);
              }}
              disabled={busy !== null}
            >
              Cancel
            </Button>
          </>
        ) : (
          <>
            <Button
              size="sm"
              onClick={() => review("approved")}
              disabled={busy !== null}
            >
              {busy === "approved" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="mr-1.5 h-3.5 w-3.5" />
              )}
              Accept and continue
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setRejecting(true)}
              disabled={busy !== null}
            >
              <X className="mr-1.5 h-3.5 w-3.5" />
              Send back
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

const GATE_BLURB: Record<RunStep["kind"], string> = {
  generate:
    "Open each document and check the signature landed where it should. Sending it back voids the signing link and asks for a fresh signature on the same document.",
  collect:
    "Open each file. Send the step back and the candidate is asked to re-upload — only the documents you mark below, or all of them if you mark none.",
  system:
    "Nothing has been emailed to these referees yet. Send it back and the candidate names different ones.",
};

const REJECT_PLACEHOLDER: Record<RunStep["kind"], string> = {
  generate: "Page 3 is unsigned — please sign every page marked with a tab.",
  collect: "The PAN card scan is cut off at the bottom — please re-upload the whole card.",
  system: "We need a manager or supervisor, not a colleague — please give us two work referees.",
};

const REJECT_CONFIRM: Record<RunStep["kind"], string> = {
  generate: "Send back for re-signing",
  collect: "Ask for these again",
  system: "Ask for new references",
};

/** The signed PDFs, with who has signed each one. */
function SignedDocuments({
  steps,
  documents,
}: {
  steps: RunStep[];
  documents: ApprovalDocument[];
}) {
  return (
    <ul className="space-y-2">
      {steps.map((step) => {
        const doc = documents.find((d) => d.kind === step.step_key);
        return (
          <li
            key={step.id}
            className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">{step.label}</p>
              <p className="truncate text-xs text-muted-foreground">
                {doc?.esign_signers.length
                  ? doc.esign_signers
                      .map(
                        (s) =>
                          `${s.name || s.role}: ${
                            s.status === "completed" ? "signed" : s.status
                          }`,
                      )
                      .join(" · ")
                  : doc?.sign_status.replace(/_/g, " ") || "Not generated"}
              </p>
            </div>
            {doc?.signed_url ? (
              <a
                href={doc.signed_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 text-xs font-medium text-brand hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Preview signed PDF
              </a>
            ) : (
              <span className="text-xs text-muted-foreground">
                No preview available
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The files the candidate filed for this step, each markable before the step
 * is sent back. Marking is how HR says *which* documents are wrong — send the
 * step back having marked none and all of them are re-asked.
 */
function SubmittedFiles({
  stepId,
  runId,
  submissions,
  onChanged,
}: {
  stepId: string;
  runId: string;
  submissions: Submission[] | undefined;
  onChanged: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const mine = (submissions ?? []).filter((s) => s.run_step_id === stepId);

  async function mark(submission: Submission, status: "approved" | "rejected") {
    setBusy(submission.id);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${runId}/submissions/${submission.id}/review`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ review_status: status, review_note: null }),
        },
      );
      if (!res.ok) throw new Error("Couldn't mark that file.");
      await onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't mark that file.");
    } finally {
      setBusy(null);
    }
  }

  if (!submissions) {
    return <div className="h-20 animate-pulse rounded-xl bg-muted" />;
  }
  if (!mine.length) {
    return (
      <p className="text-xs text-muted-foreground">
        Nothing filed against this step.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {mine.map((s) => (
        <li
          key={s.id}
          className={cn(
            "flex flex-wrap items-center gap-3 rounded-xl border bg-card p-3",
            s.review_status === "rejected"
              ? "border-destructive/40"
              : "border-border",
          )}
        >
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">{s.label}</p>
            <p className="truncate text-xs text-muted-foreground">
              {s.original_filename ?? "Uploaded"}
            </p>
          </div>

          {s.signed_url ? (
            <a
              href={s.signed_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-xs font-medium text-brand hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Preview
            </a>
          ) : null}

          <button
            type="button"
            disabled={busy === s.id}
            onClick={() =>
              mark(s, s.review_status === "rejected" ? "approved" : "rejected")
            }
            className={cn(
              "rounded-lg border px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-50",
              s.review_status === "rejected"
                ? "border-destructive bg-destructive-soft text-destructive-ink"
                : "border-border text-muted-foreground hover:border-destructive hover:text-destructive-ink",
            )}
          >
            {busy === s.id ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : s.review_status === "rejected" ? (
              "Marked — ask again"
            ) : (
              "Mark for re-upload"
            )}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** The referees the candidate named, before anything is emailed to them. */
function NamedReferences({ references }: { references: ApprovalReference[] }) {
  const live = references.filter((r) => r.status !== "superseded");
  if (!live.length) {
    return (
      <p className="text-xs text-muted-foreground">
        No references on file for this step.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {live.map((r) => (
        <li
          key={r.id}
          className="rounded-xl border border-border bg-card p-3"
        >
          <p className="text-sm font-medium text-foreground">
            {r.reference_name}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {r.reference_email}
            {r.relationship ? ` · ${r.relationship}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}
