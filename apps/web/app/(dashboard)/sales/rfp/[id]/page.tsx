"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import useSWR from "swr";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader, StatusPill, type PillTone } from "@/components/actual/kit";

type RfpStatus =
  | "extracting"
  | "awaiting_requirements_review"
  | "drafting"
  | "awaiting_rep_review"
  | "awaiting_legal_review"
  | "legal_rejected"
  | "finalizing"
  | "ready"
  | "failed"
  // legacy
  | "extracted"
  | "reviewed"
  | "generating";

interface RfpSummary {
  id: string;
  title: string | null;
  source_filename: string | null;
  status: RfpStatus;
  gap_count: number;
  deal_run_id: string | null;
  collection_id: string | null;
  legal_required: boolean;
  error_message: string | null;
  created_at: string;
  generated_at: string | null;
}

interface Requirement {
  id: string;
  rfp_id: string;
  ordinal: number;
  external_ref: string | null;
  requirement_text: string;
  category: string | null;
  source_sheet: string | null;
  source_row_index: number | null;
  source_answer_column: string | null;
  cluster_id: string | null;
  is_cluster_lead: boolean;
}

interface AnswerSource {
  document_id: string;
  document_name: string;
  chunk_id: string;
  similarity: number;
  snippet: string | null;
}

interface Answer {
  id: string;
  rfp_id: string;
  requirement_id: string;
  answer_text: string | null;
  sources: AnswerSource[];
  confidence: number | null;
  confidence_band: string | null;
  status: "draft" | "edited" | "approved" | "gap" | "rejected";
  flag_message: string | null;
  search_queries: string[];
  generated_at: string;
}

interface RfpFull {
  rfp: RfpSummary;
  requirements: Requirement[];
  answers: Answer[];
  approvals: Array<{
    id: string;
    gate_type: "rep" | "legal";
    status: "pending" | "approved" | "rejected" | "cancelled";
    notes: string | null;
    created_at: string;
  }>;
  source_filename: string;
  status: RfpStatus;
  gap_count: number;
  error_message: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  extracting: "Extracting requirements",
  extracted: "Extracted",
  reviewed: "Reviewed",
  generating: "Drafting",
  awaiting_requirements_review: "Review requirements",
  drafting: "Drafting answers",
  awaiting_rep_review: "Review answers",
  awaiting_legal_review: "Legal review",
  legal_rejected: "Re-drafting rejected items",
  finalizing: "Finalizing",
  ready: "Ready",
  failed: "Failed",
};

const STATUS_TONE: Record<string, PillTone> = {
  extracting: "blue",
  extracted: "blue",
  reviewed: "amber",
  awaiting_requirements_review: "amber",
  drafting: "amber",
  generating: "amber",
  awaiting_rep_review: "violet",
  awaiting_legal_review: "violet",
  legal_rejected: "amber",
  finalizing: "amber",
  ready: "green",
  failed: "red",
};

const POLLING_STATUSES = new Set<RfpStatus>([
  "extracting",
  "drafting",
  "generating",
  "legal_rejected",
  "finalizing",
]);

const fetcher = async (url: string): Promise<RfpFull> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function RfpDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data, error, isLoading, mutate } = useSWR<RfpFull>(
    id ? `/api/sales/rfp/${id}/full` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        latest && POLLING_STATUSES.has(latest.status) ? 3000 : 0,
    },
  );

  const [actionError, setActionError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const rfp = data?.rfp;
  const status = rfp?.status;
  const requirements = data?.requirements ?? [];
  const answersByReq = useMemo(() => {
    const m = new Map<string, Answer>();
    for (const a of data?.answers ?? []) m.set(a.requirement_id, a);
    return m;
  }, [data?.answers]);

  const callAction = useCallback(
    async (
      path: string,
      method: "POST" | "PATCH" | "DELETE" = "POST",
      body?: unknown,
    ): Promise<unknown> => {
      setActionError(null);
      setPending(true);
      try {
        const res = await fetch(path, {
          method,
          headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
          body: body !== undefined ? JSON.stringify(body) : undefined,
        });
        if (!res.ok && res.status !== 204) {
          const detail = await res.json().catch(() => ({}));
          throw new Error((detail as { detail?: string })?.detail || `Failed (${res.status})`);
        }
        await mutate();
        return res.status === 204 ? null : await res.json().catch(() => null);
      } catch (e) {
        setActionError(e instanceof Error ? e.message : "Action failed");
        return null;
      } finally {
        setPending(false);
      }
    },
    [mutate],
  );

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl space-y-3 p-6 md:p-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (error || !data || !rfp) {
    return (
      <div className="mx-auto max-w-3xl p-6 md:p-8">
        <div className="rounded border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive">
          Failed to load RFP.
        </div>
      </div>
    );
  }

  const isReady = status === "ready";
  const stage = inferStage(status);

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Sales"
        title={rfp.title || rfp.source_filename || "RFP"}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusPill tone={STATUS_TONE[status ?? ""] ?? "gray"}>
              {STATUS_LABEL[status ?? ""] ?? status}
            </StatusPill>
            {rfp.gap_count > 0 && (
              <StatusPill tone="amber">
                {rfp.gap_count} gap{rfp.gap_count === 1 ? "" : "s"}
              </StatusPill>
            )}
            {rfp.legal_required && status !== "ready" && (
              <StatusPill tone="gray">Legal review required</StatusPill>
            )}
          </span>
        }
        actions={
          isReady ? (
            <>
              <a href={`/api/sales/rfp/${rfp.id}/download?kind=filled`} download>
                <Button variant="outline">Download filled original</Button>
              </a>
              <a href={`/api/sales/rfp/${rfp.id}/download?kind=summary`} download>
                <Button>Download summary DOCX</Button>
              </a>
            </>
          ) : undefined
        }
      />

      <StageStepper current={stage} legalRequired={rfp.legal_required} />

      {rfp.error_message && (
        <div className="rounded border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive">
          {rfp.error_message}
        </div>
      )}
      {actionError && (
        <div className="rounded border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive">
          {actionError}
        </div>
      )}

      {(status === "extracting") && (
        <Spinner label="Extracting requirements from the file…" />
      )}

      {(status === "awaiting_requirements_review" || status === "extracted" || status === "reviewed") && (
        <RequirementsReview
          rfpId={rfp.id}
          requirements={requirements}
          pending={pending}
          mutate={mutate}
          onApprove={() => callAction(`/api/sales/rfp/${rfp.id}/requirements/approve`)}
        />
      )}

      {(status === "drafting" || status === "generating" || status === "legal_rejected") && (
        <Spinner label={status === "legal_rejected" ? "Re-drafting rejected items…" : "Drafting answers…"} />
      )}

      {(status === "awaiting_rep_review" || status === "awaiting_legal_review") && (
        <AnswerReview
          rfpId={rfp.id}
          requirements={requirements}
          answersByReq={answersByReq}
          mode={status === "awaiting_legal_review" ? "legal" : "rep"}
          pending={pending}
          callAction={callAction}
        />
      )}

      {(status === "finalizing") && (
        <Spinner label="Generating final outputs…" />
      )}

      {status === "ready" && (
        <ReadyView
          requirements={requirements}
          answersByReq={answersByReq}
          gapCount={rfp.gap_count}
        />
      )}
    </div>
  );
}


// ── Stage stepper ────────────────────────────────────────────────────────

type Stage = "extract" | "review_reqs" | "draft" | "review_answers" | "legal" | "done";

function inferStage(status: RfpStatus | undefined): Stage {
  switch (status) {
    case "extracting":
      return "extract";
    case "extracted":
    case "reviewed":
    case "awaiting_requirements_review":
      return "review_reqs";
    case "drafting":
    case "generating":
    case "legal_rejected":
      return "draft";
    case "awaiting_rep_review":
      return "review_answers";
    case "awaiting_legal_review":
      return "legal";
    case "finalizing":
    case "ready":
      return "done";
    default:
      return "extract";
  }
}

function StageStepper({ current, legalRequired }: { current: Stage; legalRequired: boolean }) {
  const steps: Array<{ key: Stage; label: string }> = [
    { key: "extract", label: "Extract" },
    { key: "review_reqs", label: "Review requirements" },
    { key: "draft", label: "Draft answers" },
    { key: "review_answers", label: "Review answers" },
  ];
  if (legalRequired) steps.push({ key: "legal", label: "Legal review" });
  steps.push({ key: "done", label: "Ready" });

  const currentIdx = steps.findIndex((s) => s.key === current);
  return (
    <ol className="flex flex-wrap items-center gap-2 text-xs">
      {steps.map((step, idx) => {
        const isDone = idx < currentIdx;
        const isCurrent = idx === currentIdx;
        return (
          <li key={step.key} className="flex items-center gap-2">
            <span
              className={
                "flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold " +
                (isDone
                  ? "bg-success text-white"
                  : isCurrent
                    ? "bg-brand text-white"
                    : "bg-muted text-muted-foreground")
              }
            >
              {isDone ? "✓" : idx + 1}
            </span>
            <span className={isCurrent ? "font-bold" : "text-muted-foreground"}>{step.label}</span>
            {idx < steps.length - 1 && <span className="mx-1 text-border">›</span>}
          </li>
        );
      })}
    </ol>
  );
}


// ── Sub-views ────────────────────────────────────────────────────────────


function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-amber-tint p-4 text-sm font-medium text-amber">
      <span className="inline-block h-3 w-3 animate-pulse rounded-full bg-amber" />
      {label}
    </div>
  );
}


function RequirementsReview({
  rfpId,
  requirements,
  pending,
  mutate,
  onApprove,
}: {
  rfpId: string;
  requirements: Requirement[];
  pending: boolean;
  mutate: () => Promise<unknown>;
  onApprove: () => Promise<unknown>;
}) {
  const [edits, setEdits] = useState<Map<string, { text: string; category: string | null }>>(
    new Map(requirements.map((r) => [r.id, { text: r.requirement_text, category: r.category }])),
  );
  const [savingId, setSavingId] = useState<string | null>(null);
  const [addingText, setAddingText] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    setEdits(
      new Map(requirements.map((r) => [r.id, { text: r.requirement_text, category: r.category }])),
    );
  }, [requirements]);

  const saveOne = async (id: string) => {
    const edit = edits.get(id);
    if (!edit) return;
    setSavingId(id);
    try {
      await fetch(`/api/sales/rfp/${rfpId}/requirements/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirement_text: edit.text, category: edit.category }),
      });
      await mutate();
    } finally {
      setSavingId(null);
    }
  };

  const removeOne = async (id: string) => {
    await fetch(`/api/sales/rfp/${rfpId}/requirements/${id}`, { method: "DELETE" });
    await mutate();
  };

  const addOne = async () => {
    if (!addingText.trim()) return;
    setAdding(true);
    try {
      await fetch(`/api/sales/rfp/${rfpId}/requirements`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirement_text: addingText }),
      });
      setAddingText("");
      await mutate();
    } finally {
      setAdding(false);
    }
  };

  return (
    <section className="space-y-3">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-medium">
          {requirements.length} requirement{requirements.length === 1 ? "" : "s"} — review & edit
        </h2>
        <Button onClick={onApprove} disabled={pending || requirements.length === 0}>
          {pending ? "Submitting…" : "Approve & draft answers"}
        </Button>
      </header>

      <ul className="space-y-2">
        {requirements.map((r) => {
          const edit = edits.get(r.id) ?? { text: r.requirement_text, category: r.category };
          return (
            <li key={r.id} className="rounded-xl border border-border bg-card p-3">
              <div className="flex items-start gap-3">
                <div className="w-12 shrink-0 pt-2 text-xs font-medium text-muted-foreground">
                  {r.external_ref || `#${r.ordinal + 1}`}
                </div>
                <div className="flex-1 space-y-2">
                  <Textarea
                    value={edit.text}
                    onChange={(e) => {
                      const next = new Map(edits);
                      next.set(r.id, { ...edit, text: e.target.value });
                      setEdits(next);
                    }}
                    rows={2}
                  />
                  <Input
                    placeholder="Category (e.g. security, integration)"
                    value={edit.category ?? ""}
                    onChange={(e) => {
                      const next = new Map(edits);
                      next.set(r.id, { ...edit, category: e.target.value || null });
                      setEdits(next);
                    }}
                  />
                </div>
                <div className="flex flex-col gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => saveOne(r.id)}
                    disabled={savingId === r.id}
                  >
                    {savingId === r.id ? "Saving…" : "Save"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => removeOne(r.id)}>
                    Remove
                  </Button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <div className="rounded border border-dashed bg-muted/40 p-3">
        <p className="mb-2 text-xs text-muted-foreground">Add a missed requirement</p>
        <div className="flex gap-2">
          <Input
            value={addingText}
            onChange={(e) => setAddingText(e.target.value)}
            placeholder="What does the buyer want?"
          />
          <Button onClick={addOne} disabled={adding || !addingText.trim()}>
            {adding ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>
    </section>
  );
}


function AnswerReview({
  rfpId,
  requirements,
  answersByReq,
  mode,
  pending,
  callAction,
}: {
  rfpId: string;
  requirements: Requirement[];
  answersByReq: Map<string, Answer>;
  mode: "rep" | "legal";
  pending: boolean;
  callAction: (path: string, method?: "POST" | "PATCH" | "DELETE", body?: unknown) => Promise<unknown>;
}) {
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  const [rejectNotes, setRejectNotes] = useState("");

  const toggleReject = (reqId: string) => {
    const next = new Set(rejected);
    if (next.has(reqId)) next.delete(reqId);
    else next.add(reqId);
    setRejected(next);
  };

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">
          {mode === "legal" ? "Legal review" : "Answer review"} —{" "}
          {requirements.length} requirement{requirements.length === 1 ? "" : "s"}
        </h2>
        {mode === "rep" && (
          <Button
            onClick={() => callAction(`/api/sales/rfp/${rfpId}/approve-rep`)}
            disabled={pending}
          >
            Approve all
          </Button>
        )}
      </header>

      <ul className="space-y-3">
        {requirements.map((r) => {
          const a = answersByReq.get(r.id);
          return (
            <AnswerCard
              key={r.id}
              rfpId={rfpId}
              requirement={r}
              answer={a}
              mode={mode}
              isRejected={rejected.has(r.id)}
              onToggleReject={() => toggleReject(r.id)}
              callAction={callAction}
            />
          );
        })}
      </ul>

      {mode === "legal" && (
        <div className="space-y-3 rounded border bg-muted/40 p-4">
          <p className="text-sm font-medium">Legal decision</p>
          <Textarea
            placeholder="Notes for the rep (required if rejecting)…"
            rows={3}
            value={rejectNotes}
            onChange={(e) => setRejectNotes(e.target.value)}
          />
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">
              {rejected.size > 0
                ? `${rejected.size} requirement(s) marked for re-draft.`
                : "No rejections — clicking Approve sends the package to export."}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  callAction(`/api/sales/rfp/${rfpId}/reject-legal`, "POST", {
                    notes: rejectNotes,
                    rejected_requirement_ids: Array.from(rejected),
                  })
                }
                disabled={pending || !rejectNotes.trim() || rejected.size === 0}
              >
                Send back ({rejected.size})
              </Button>
              <Button
                onClick={() => callAction(`/api/sales/rfp/${rfpId}/approve-legal`)}
                disabled={pending}
              >
                Approve and finalize
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}


function AnswerCard({
  rfpId,
  requirement,
  answer,
  mode,
  isRejected,
  onToggleReject,
  callAction,
}: {
  rfpId: string;
  requirement: Requirement;
  answer: Answer | undefined;
  mode: "rep" | "legal";
  isRejected: boolean;
  onToggleReject: () => void;
  callAction: (path: string, method?: "POST" | "PATCH" | "DELETE", body?: unknown) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(answer?.answer_text ?? "");
  useEffect(() => setDraft(answer?.answer_text ?? ""), [answer?.answer_text]);

  const band = answer?.confidence_band;
  const isGap = answer?.status === "gap" || answer?.status === "rejected";

  return (
    <li
      className={
        "rounded-xl border border-border bg-card p-4 " +
        (isRejected ? "border-amber-400 ring-1 ring-amber-200" : "")
      }
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-muted-foreground">
            {requirement.external_ref || `R${requirement.ordinal + 1}`}
            {requirement.category && (
              <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                {requirement.category}
              </span>
            )}
            {requirement.source_sheet && requirement.source_row_index && (
              <span className="ml-2 text-muted-foreground">
                · {requirement.source_sheet} row {requirement.source_row_index}
              </span>
            )}
          </p>
          <p className="mt-1 text-sm">{requirement.requirement_text}</p>
        </div>
        {band && (
          <StatusPill
            tone={band === "high" ? "green" : band === "medium" ? "amber" : "red"}
          >
            {band} confidence
          </StatusPill>
        )}
      </div>

      <div className={isGap ? "rounded border border-destructive/40 bg-destructive-soft p-3" : "rounded bg-muted/40 p-3"}>
        {editing ? (
          <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} />
        ) : (
          <p className={"whitespace-pre-wrap text-sm " + (isGap ? "text-destructive" : "")}>
            {answer?.answer_text ||
              (isGap
                ? `⚠ ${answer?.flag_message || "Gap — flagged for review."}`
                : "(No answer generated yet.)")}
          </p>
        )}
        {answer?.sources && answer.sources.length > 0 && !editing && (
          <p className="mt-2 text-xs text-muted-foreground">
            Sources: {answer.sources.slice(0, 3).map((s) => s.document_name).join(", ")}
          </p>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {mode === "rep" &&
          (editing ? (
            <>
              <Button
                size="sm"
                onClick={async () => {
                  if (!answer) return;
                  await callAction(
                    `/api/sales/rfp/${rfpId}/answers/${answer.id}`,
                    "PATCH",
                    { answer_text: draft },
                  );
                  setEditing(false);
                }}
              >
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
                Edit
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  callAction(
                    `/api/sales/rfp/${rfpId}/answers/${requirement.id}/regenerate`,
                    "POST",
                  )
                }
              >
                Regenerate
              </Button>
            </>
          ))}
        {mode === "legal" && (
          <Button
            size="sm"
            variant={isRejected ? "primary" : "outline"}
            onClick={onToggleReject}
          >
            {isRejected ? "Reject (selected)" : "Reject this row"}
          </Button>
        )}
      </div>
    </li>
  );
}


function ReadyView({
  requirements,
  answersByReq,
  gapCount,
}: {
  requirements: Requirement[];
  answersByReq: Map<string, Answer>;
  gapCount: number;
}) {
  return (
    <section className="space-y-3">
      {gapCount > 0 && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {gapCount} requirement{gapCount === 1 ? " was" : "s were"} flagged as a gap. They are
          highlighted in red in both the filled original and the summary.
        </div>
      )}
      <ul className="space-y-3">
        {requirements.map((r) => {
          const a = answersByReq.get(r.id);
          const isGap = a?.status === "gap" || a?.status === "rejected";
          return (
            <li key={r.id} className="rounded-xl border border-border bg-card p-4">
              <p className="text-xs font-semibold text-muted-foreground">
                {r.external_ref || `R${r.ordinal + 1}`}
                {r.category && (
                  <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
                    {r.category}
                  </span>
                )}
              </p>
              <p className="mt-1 text-sm font-medium">{r.requirement_text}</p>
              <div
                className={
                  "mt-2 rounded p-3 text-sm " +
                  (isGap ? "border border-destructive/40 bg-destructive-soft text-destructive" : "bg-muted/40")
                }
              >
                {a?.answer_text ||
                  (isGap ? `⚠ ${a?.flag_message ?? "Gap"}` : "(No answer.)")}
              </div>
              {a?.sources && a.sources.length > 0 && !isGap && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Sources: {a.sources.slice(0, 3).map((s) => s.document_name).join(", ")}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
