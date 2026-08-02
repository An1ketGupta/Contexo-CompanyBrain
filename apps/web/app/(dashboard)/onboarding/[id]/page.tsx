"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  CheckCircle2,
  ExternalLink,
  FileSignature,
  Loader2,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill, type PillTone } from "@/components/actual/kit";
import { DocumentReviewPanel } from "@/components/onboarding/document-review-panel";
import { DocumentsPanel } from "@/components/onboarding/documents-panel";
import {
  StageBoard,
  panelStageGroup,
} from "@/components/onboarding/stage-board";
import { StepApprovalPanel } from "@/components/onboarding/step-approval-panel";
import {
  builtInPanelFor,
  type RunStep,
} from "@/components/onboarding/step-panel";

interface ReferenceRow {
  id: string;
  reference_name: string;
  reference_email: string;
  reference_phone: string | null;
  relationship: string | null;
  status: string;
  email_sent_at: string | null;
  opened_at: string | null;
  submitted_at: string | null;
  reminder_count: number;
  response_worked_together_months: number | null;
  response_would_recommend: boolean | null;
  response_strengths: string | null;
  response_concerns: string | null;
  response_role_description: string | null;
}

interface EsignSigner {
  role: string;
  name: string;
  status: string; // "pending" | "completed"
  completed_at: string | null;
}

interface DocumentRow {
  id: string;
  kind: string;
  storage_path: string;
  signed_url: string | null;
  signed_pdf_url: string | null;
  sign_status: string;
  signed_pdf_path: string | null;
  signed_uploaded_at: string | null;
  file_bytes: number | null;
  hr_edited_storage_path: string | null;
  hr_edited_pdf_path: string | null;
  hr_edited_at: string | null;
  hr_edit_revision: number;
  esign_envelope_id: string | null;
  esign_status: string | null;
  esign_signing_url: string | null;
  esign_completed_at: string | null;
  esign_signers: EsignSigner[];
  created_at: string;
  updated_at: string;
}

interface EventRow {
  id: string;
  actor_kind: string;
  event_type: string;
  message: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

interface RunDetail {
  id: string;
  candidate_name: string;
  candidate_email: string;
  candidate_phone: string | null;
  role_title: string;
  designation: string | null;
  ctc_amount: number | null;
  ctc_currency: string | null;
  start_date: string;
  work_location: string | null;
  probation_period_months: number | null;
  reporting_manager_name: string | null;
  status: string;
  blocked_reason: string | null;
  blocked_template_kind: string | null;
  loi_sent_to_hr_at: string | null;
  loi_signed_at: string | null;
  bgv_sent_at: string | null;
  bgv_completed_at: string | null;
  appointment_sent_at: string | null;
  policies_assigned_at: string | null;
  policies_acknowledged_at: string | null;
  induction_sent_at: string | null;
  completed_at: string | null;
  loi_approved_for_signing_at: string | null;
  loi_draft_edited_at: string | null;
  loi_draft_revision: number;
  references_form_expires_at: string | null;
  references_submitted_at: string | null;
  references_reminder_count: number;
  references_last_reminder_at: string | null;
  created_at: string;
  updated_at: string;
  references: ReferenceRow[];
  documents: DocumentRow[];
  events: EventRow[];
  steps: RunStep[];
}

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  loi_generating: "Preparing LOI from template",
  loi_pending_hr_review: "Review LOI draft",
  loi_pending_hr_sign: "Awaiting HR signature",
  loi_pending_esign_signature: "Signing LOI",
  loi_signed_uploaded: "LOI signed — sending",
  loi_sent_to_candidate: "LOI sent",
  awaiting_candidate_references: "Awaiting candidate references",
  bgv_pending: "BGV in progress",
  bgv_complete: "BGV complete",
  appointment_bundle_generating: "Preparing Appointment Letter + NDA from templates",
  appointment_pending_hr_review: "Awaiting HR approval",
  appointment_sent_to_candidate: "Appointment + NDA sent",
  policies_assigned: "Policies assigned",
  policies_acknowledged: "Policies acknowledged",
  induction_generating: "Preparing induction from template",
  induction_sent: "Induction sent",
  completed: "Completed",
  blocked_missing_template: "Blocked — missing template",
  blocked_template_drift: "Blocked — template changed",
  failed: "Failed",
  cancelled: "Cancelled",
  // What a step an org composed writes, since it has no legacy label to reuse.
  // Generic on purpose — the step's own name is right there in the pipeline
  // panel, so the pill says what is happening rather than to what.
  step_active: "In progress",
  step_generating: "Preparing document from template",
  step_pending_hr_review: "Review draft",
  step_pending_signature: "Awaiting signature",
  step_pending_hr_approval: "Waiting on your review",
  awaiting_candidate_documents: "Awaiting candidate documents",
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

function expiryLabel(iso: string | null): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return `expired ${relativeTime(iso)}`;
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "expires in under a minute";
  if (min < 60) return `expires in ${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `expires in ${hr}h`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `expires in ${d}d`;
  return `expires ${new Date(iso).toLocaleDateString()}`;
}

export default function OnboardingDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  const { data, error, isLoading, mutate } = useSWR<RunDetail>(
    id ? `/api/onboarding/runs/${id}` : null,
    fetcher,
    { refreshInterval: 8_000 },
  );

  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function nudgeCandidate() {
    setBusy("nudge-candidate");
    setActionError(null);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${id}/references-nudge`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't send the reminder.",
        );
        return;
      }
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function extendReferencesToken() {
    if (
      !confirm(
        "Generate a new references form link and email it to the candidate? The old link will stop working.",
      )
    ) {
      return;
    }
    setBusy("extend-token");
    setActionError(null);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${id}/references-token/extend`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't extend the form link.",
        );
        return;
      }
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function submitHrReferencesOverride(
    refs: { name: string; email: string; phone?: string; relationship?: string }[],
  ) {
    setBusy("refs-override");
    setActionError(null);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${id}/references-override`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ references: refs }),
        },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't submit references.",
        );
        return;
      }
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function resume() {
    setBusy("resume");
    setActionError(null);
    try {
      const res = await fetch(`/api/onboarding/runs/${id}/resume`, { method: "POST" });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string; message?: string };
        setActionError(body.detail || body.message || "Couldn't re-run the agent.");
        return;
      }
      setTimeout(() => mutate(), 800);
    } finally {
      setBusy(null);
    }
  }

  async function cancel() {
    if (!confirm("Cancel this onboarding run? This can't be undone.")) return;
    setBusy("cancel");
    try {
      await fetch(`/api/onboarding/runs/${id}/cancel`, { method: "POST" });
      await mutate();
      globalMutate("/api/onboarding/runs");
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-8">
        <Skeleton className="mb-4 h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-8">
        <p className="text-sm font-medium text-destructive">
          Couldn&apos;t load this onboarding run.{" "}
          <Link
            href="/onboarding"
            className="underline hover:no-underline"
          >
            Back to list
          </Link>
        </p>
      </div>
    );
  }

  const isBlocked = data.status === "blocked_missing_template";
  const steps = data.steps ?? [];
  // Where the run actually is, asked of its own steps. A step an org composed
  // beyond the built-ins answers null and shows no panel, and so does a run
  // that has finished — its documents live in the archive below instead.
  const currentGroup = panelStageGroup(steps, data.status);
  const currentStep = currentGroup?.[0] ?? null;
  // The approval gate is not one of the five built-in panels — any step can
  // reach it, including one an org composed itself, so it is keyed off the
  // status rather than off which document the step happens to render.
  const awaitingReview = currentStep?.status === "pending_hr_approval";
  // While the gate is up it is the only thing to act on. The built-in panels
  // would render their idle state underneath it — a document row with nothing
  // to click — which reads as a second, contradictory place to look.
  const panel = awaitingReview ? null : builtInPanelFor(currentStep);
  const ctc =
    data.ctc_amount !== null && data.ctc_amount !== undefined
      ? `${data.ctc_currency || "INR"} ${data.ctc_amount.toLocaleString()}`
      : "—";

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <Link
        href="/onboarding"
        className="mb-4 inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to onboarding
      </Link>

      <header className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="mb-1 text-[13px] font-bold text-brand">Onboarding</p>
            <h1 className="text-3xl font-extrabold tracking-tight text-foreground">
              {data.candidate_name}
            </h1>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {data.role_title} · starts {data.start_date}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={resume}
              disabled={busy === "resume"}
            >
              {busy === "resume" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              )}
              Re-run agent
            </Button>
            {data.status !== "completed" && data.status !== "cancelled" ? (
              <Button
                variant="outline"
                size="sm"
                onClick={cancel}
                disabled={busy === "cancel"}
              >
                Cancel
              </Button>
            ) : null}
          </div>
        </div>

        <div className="mt-3">
          <StatusPill
            tone={
              (data.status === "completed"
                ? "green"
                : isBlocked || data.status === "failed"
                  ? "red"
                  : "blue") as PillTone
            }
          >
            {STATUS_LABELS[data.status] || data.status}
          </StatusPill>
        </div>
      </header>

      {/* Stage board — this run's own steps as columns, candidate box parked in
          whichever one it's sitting in. */}
      <section className="mb-8">
        <StageBoard
          run={data}
          steps={steps}
          statusLabel={STATUS_LABELS[data.status] || data.status}
        />
      </section>

      {actionError ? (
        <div className="mb-4 rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-sm font-medium text-destructive">
          {actionError}
        </div>
      ) : null}

      {isBlocked ? (
        <div className="mb-6 rounded-2xl border border-amber/30 bg-amber-tint p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber" />
            <div className="flex-1">
              <p className="text-sm font-bold text-amber">
                {(data.blocked_template_kind || "document")
                  .replace(/_/g, " ")
                  .toUpperCase()}{" "}
                generation is blocked
              </p>
              {data.blocked_reason ? (
                <p className="mt-1 text-xs text-amber/90">{data.blocked_reason}</p>
              ) : null}

              {/* When the block is a value nobody has supplied, HR answers it
                  here. The template link stays for the other blocks — a
                  template that was never uploaded has no fields to fill. */}
              <BlockingFieldsForm runId={id} onSaved={mutate} />

              <Link
                href="/document-templates"
                className="mt-3 inline-block text-xs font-bold text-amber underline hover:no-underline"
              >
                Open templates →
              </Link>
            </div>
          </div>
        </div>
      ) : null}

      {data.status !== "blocked_missing_template" && data.blocked_reason ? (
        <div className="mb-6 rounded-2xl border border-destructive/30 bg-destructive-soft p-4">
          <div className="flex items-start gap-3">
            <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
            <div className="flex-1">
              <p className="text-sm font-bold text-destructive">
                Agent failed
              </p>
              <p className="mt-1 text-xs text-destructive/90">
                {data.blocked_reason}
              </p>
            </div>
          </div>
        </div>
      ) : null}

      {/* Summary card */}
      <section className="mb-8 grid grid-cols-2 gap-x-6 gap-y-3 rounded-2xl border border-border bg-card p-4 sm:grid-cols-4">
        <Field label="CTC" value={ctc} />
        <Field label="Designation" value={data.designation || data.role_title} />
        <Field
          label="Reporting manager"
          value={data.reporting_manager_name || "—"}
        />
        <Field
          label="Work location"
          value={data.work_location || "—"}
        />
      </section>

      {/* The gate, first and above everything: the run is stopped here and
          nothing else on the page can move it. Shown for any step in
          `pending_hr_approval`, built-in or composed by the org. */}
      {awaitingReview && currentGroup ? (
        <>
          <SectionHeader
            icon={ShieldCheck}
            title={`Your review: ${currentStep?.bundle_label ?? currentStep?.label}`}
          />
          <div className="mb-12">
            <StepApprovalPanel
              runId={data.id}
              steps={currentGroup}
              documents={data.documents}
              references={data.references}
              onReviewed={mutate}
            />
          </div>
        </>
      ) : null}

      {/* The shelf to download from: what the candidate filed on steps HR has
          already accepted, plus the letters the company signed and sent back.
          A step still at its gate is reviewed in the panel above and
          deliberately absent here; two lists of the same files, each with its
          own controls, read as two contradictory decisions. */}
      <div className="mb-8">
        <DocumentsPanel
          runId={data.id}
          steps={steps}
          documents={data.documents}
        />
      </div>

      {/* Only the step the run is actually sitting in. The rest of the pipeline
          is in the board above — an empty panel for a step nobody has reached
          yet is noise, not information.

          Every step that generates a document gets the same panel, whatever the
          org called it: read it, correct it, send it for signature. */}
      {panel === "document" && currentGroup ? (
        <>
          <SectionHeader
            icon={FileSignature}
            title={currentGroup[0].bundle_label ?? currentGroup[0].label}
          />
          {/* `#offer-bundle` is where the "documents are ready" email lands.
              Only one document panel is ever on the page, so whichever step the
              run is at, this is what that link means. */}
          <div
            id="offer-bundle"
            className="mb-12 rounded-2xl border border-border bg-card p-4"
          >
            <DocumentReviewPanel
              runId={data.id}
              steps={currentGroup}
              documents={data.documents}
              candidateName={data.candidate_name}
              onChanged={mutate}
            />
          </div>
        </>
      ) : null}

      {panel === "bgv" && currentStep ? (
        <>
          <SectionHeader icon={ShieldCheck} title={currentStep.label} />
          <div className="mb-12 rounded-2xl border border-border bg-card p-4">
            <BgvPanel
              data={data}
              references={data.references}
              busy={busy}
              onHrOverride={submitHrReferencesOverride}
              onNudge={nudgeCandidate}
              onExtendToken={extendReferencesToken}
            />
          </div>
        </>
      ) : null}

      {panel === "policies" && currentStep ? (
        <>
          <SectionHeader icon={CheckCircle2} title={currentStep.label} />
          <div className="mb-12 rounded-2xl border border-border bg-card p-4">
            <p className="text-sm font-medium">{currentStep.label}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Assigned: {relativeTime(data.policies_assigned_at)}
              <br />
              Acknowledged: {relativeTime(data.policies_acknowledged_at)}
            </p>
          </div>
        </>
      ) : null}

      {panel === "induction" && currentStep ? (
        <>
          <SectionHeader icon={CheckCircle2} title={currentStep.label} />
          <div className="mb-12 rounded-2xl border border-border bg-card p-4">
            <p className="text-sm font-medium">{currentStep.label}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Sent: {relativeTime(data.induction_sent_at)}
            </p>
            <InductionLink data={data} step={currentStep} />
          </div>
        </>
      ) : null}
    </div>
  );
}

function SectionHeader({
  icon: Icon,
  title,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
}) {
  return (
    <h2 className="mb-3 flex items-center gap-2 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
      <Icon className="h-3.5 w-3.5 text-brand" />
      {title}
    </h2>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-sm font-bold text-foreground">{value}</p>
    </div>
  );
}

interface BlockingField {
  internal_name: string;
  label: string;
  data_type: string;
  description: string | null;
  example_value: string | null;
  code: string;
  message: string;
  value: string;
}

interface BlockingFields {
  document_kind: string | null;
  template_name: string | null;
  generated_document_id: string | null;
  fields: BlockingField[];
}

function inputTypeFor(dataType: string): string {
  switch (dataType) {
    case "date":
      return "date";
    case "email":
      return "email";
    case "phone":
      return "tel";
    case "number":
    case "currency":
      return "text";
    default:
      return "text";
  }
}

/**
 * The fields the last generation attempt blocked on, as a form.
 *
 * Renders nothing unless there is something to type: a run blocked because no
 * template was ever uploaded has no fields, and an empty box under that message
 * would read as "fill this in to continue" when there is nothing to fill.
 */
function BlockingFieldsForm({
  runId,
  onSaved,
}: {
  runId: string;
  onSaved: () => void;
}) {
  const { data, mutate: refetch } = useSWR<BlockingFields>(
    runId ? `/api/onboarding/runs/${runId}/blocking-fields` : null,
    fetcher,
  );
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fields = data?.fields ?? [];
  if (fields.length === 0) return null;

  const valueOf = (f: BlockingField) => edits[f.internal_name] ?? f.value;
  const incomplete = fields.some((f) => !valueOf(f).trim());

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const values: Record<string, string> = {};
      for (const f of fields) values[f.internal_name] = valueOf(f);

      const res = await fetch(`/api/onboarding/runs/${runId}/field-values`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setError(
          body.detail || body.message || "Couldn't save these values.",
        );
        return;
      }
      setEdits({});
      // The agent re-runs on the server; both views need the new state.
      await Promise.all([refetch(), onSaved()]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-3 space-y-3 rounded-xl border border-amber/30 bg-card p-3">
      <p className="text-xs font-bold text-foreground">
        Fill in {fields.length === 1 ? "this value" : "these values"} to continue
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((f) => (
          <div key={f.internal_name}>
            <label
              htmlFor={`field-${f.internal_name}`}
              className="font-mono text-[10px] font-bold uppercase tracking-wider text-muted-foreground"
            >
              {f.label}
            </label>
            {f.data_type === "address" ? (
              <textarea
                id={`field-${f.internal_name}`}
                rows={2}
                placeholder={f.example_value || ""}
                className="mt-1 w-full rounded-lg border border-input bg-background px-2 py-1.5 text-xs"
                value={valueOf(f)}
                onChange={(e) =>
                  setEdits((v) => ({ ...v, [f.internal_name]: e.target.value }))
                }
              />
            ) : (
              <input
                id={`field-${f.internal_name}`}
                type={inputTypeFor(f.data_type)}
                placeholder={f.example_value || ""}
                className="mt-1 h-8 w-full rounded-lg border border-input bg-background px-2 text-xs"
                value={valueOf(f)}
                onChange={(e) =>
                  setEdits((v) => ({ ...v, [f.internal_name]: e.target.value }))
                }
              />
            )}
            <p className="mt-1 text-[11px] text-muted-foreground">
              {f.description || f.message}
            </p>
          </div>
        ))}
      </div>

      {error ? (
        <p className="text-xs font-medium text-destructive">{error}</p>
      ) : null}

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={saving || incomplete}>
          {saving ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : null}
          Save and continue
        </Button>
        <p className="text-[11px] text-muted-foreground">
          {incomplete
            ? "Every field is required by the template."
            : "The agent picks the run back up automatically."}
        </p>
      </div>
    </div>
  );
}

function HrReferencesOverride({
  busy,
  onSubmit,
}: {
  busy: string | null;
  onSubmit: (
    refs: { name: string; email: string; phone?: string; relationship?: string }[],
  ) => void;
}) {
  const [refs, setRefs] = useState([
    { name: "", email: "", phone: "", relationship: "" },
    { name: "", email: "", phone: "", relationship: "" },
  ]);
  const [open, setOpen] = useState(false);

  function update(idx: number, patch: Partial<(typeof refs)[number]>) {
    setRefs((rs) => rs.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-xs font-medium text-foreground underline hover:no-underline"
      >
        Enter references manually instead
      </button>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-border bg-card p-3">
      <p className="text-xs font-bold">Enter references on the candidate&apos;s behalf</p>
      {refs.map((r, idx) => (
        <div key={idx} className="grid gap-2 sm:grid-cols-2">
          <input
            placeholder="Full name"
            className="h-8 rounded-lg border border-input bg-background px-2 text-xs"
            value={r.name}
            onChange={(e) => update(idx, { name: e.target.value })}
          />
          <input
            placeholder="Email"
            className="h-8 rounded-lg border border-input bg-background px-2 text-xs"
            value={r.email}
            onChange={(e) => update(idx, { email: e.target.value })}
          />
          <input
            placeholder="Phone (optional)"
            className="h-8 rounded-lg border border-input bg-background px-2 text-xs"
            value={r.phone}
            onChange={(e) => update(idx, { phone: e.target.value })}
          />
          <input
            placeholder="Relationship (e.g. Manager at Acme)"
            className="h-8 rounded-lg border border-input bg-background px-2 text-xs"
            value={r.relationship}
            onChange={(e) => update(idx, { relationship: e.target.value })}
          />
        </div>
      ))}
      <div className="flex items-center gap-2 pt-1">
        <Button
          size="sm"
          disabled={busy === "refs-override"}
          onClick={() => {
            const valid = refs
              .filter((r) => r.name.trim() && r.email.trim())
              .map((r) => ({
                name: r.name.trim(),
                email: r.email.trim(),
                phone: r.phone.trim() || undefined,
                relationship: r.relationship.trim() || undefined,
              }));
            if (valid.length < 1) {
              alert("Add at least one reference with name + email.");
              return;
            }
            onSubmit(valid);
          }}
        >
          {busy === "refs-override" ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : null}
          Submit references
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function BgvPanel({
  data,
  references,
  busy,
  onHrOverride,
  onNudge,
  onExtendToken,
}: {
  data: RunDetail;
  references: ReferenceRow[];
  busy: string | null;
  onHrOverride: (
    refs: { name: string; email: string; phone?: string; relationship?: string }[],
  ) => void;
  onNudge: () => void;
  onExtendToken: () => void;
}) {
  // The agent waits here until the candidate names their referees, which is
  // exactly what `references_submitted_at` records. It used to read the run
  // status, but `awaiting_candidate_references` is a value the status ladder no
  // longer writes at all — the step engine reports `bgv_pending`, so this was
  // always false and the nudge and manual-entry controls never appeared.
  const awaitingCandidate = !data.references_submitted_at;

  const formExpired =
    data.references_form_expires_at != null &&
    new Date(data.references_form_expires_at) < new Date();

  const formExpiryLabel = data.references_form_expires_at
    ? `Form link ${expiryLabel(data.references_form_expires_at)}`
    : null;

  if (awaitingCandidate && references.length === 0) {
    return (
      <div className="space-y-4">
        <div>
          <p className="text-sm text-muted-foreground">
            Waiting for the candidate to submit references via the form link in
            their LOI email.
            {data.references_reminder_count > 0
              ? ` Reminders sent: ${data.references_reminder_count}.`
              : ""}
          </p>

          {formExpiryLabel ? (
            <p
              className={
                "mt-1 text-xs " +
                (formExpired
                  ? "font-medium text-destructive"
                  : "text-muted-foreground")
              }
            >
              {formExpiryLabel}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {formExpired ? (
            <Button
              size="sm"
              variant="outline"
              onClick={onExtendToken}
              disabled={busy === "extend-token"}
            >
              {busy === "extend-token" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              )}
              Extend form link (14 days)
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={onNudge}
              disabled={busy === "nudge-candidate"}
            >
              {busy === "nudge-candidate" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Bell className="mr-1.5 h-3.5 w-3.5" />
              )}
              Nudge candidate
            </Button>
          )}
        </div>

        <div className="border-t border-border pt-3">
          <HrReferencesOverride busy={busy} onSubmit={onHrOverride} />
        </div>
      </div>
    );
  }

  if (references.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No references on file yet — the candidate will submit them with their
        LOI response.
      </p>
    );
  }
  return (
    <ul className="space-y-3">
      {references.map((r) => {
        const submitted = r.status === "submitted";
        const initials = r.reference_name
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .map((part) => part[0]!.toUpperCase())
          .join("");
        return (
          <li
            key={r.id}
            className="rounded-xl border border-border bg-muted/40 p-3.5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-tint text-xs font-bold text-brand">
                  {initials}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">
                    {r.reference_name}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {r.reference_email}
                  </p>
                </div>
              </div>
              <StatusPill
                tone={submitted ? "green" : "blue"}
                className="shrink-0"
              >
                {r.status}
              </StatusPill>
            </div>

            <p className="mt-2 text-xs text-muted-foreground">
              {r.relationship || "Professional reference"} ·{" "}
              {submitted
                ? `Responded ${relativeTime(r.submitted_at)}`
                : r.opened_at
                  ? `Opened ${relativeTime(r.opened_at)}`
                  : r.email_sent_at
                    ? `Sent ${relativeTime(r.email_sent_at)}`
                    : "Pending"}
            </p>

            {submitted ? (
              <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg border border-border bg-card p-3 text-xs">
                <ReferenceField label="Worked together">
                  {r.response_worked_together_months ?? "—"} months
                </ReferenceField>
                <ReferenceField label="Would recommend">
                  {r.response_would_recommend === null
                    ? "—"
                    : r.response_would_recommend
                      ? "Yes"
                      : "No"}
                </ReferenceField>
                {r.response_role_description ? (
                  <ReferenceField label="Role" full>
                    {r.response_role_description}
                  </ReferenceField>
                ) : null}
                {r.response_strengths ? (
                  <ReferenceField label="Strengths" full>
                    {r.response_strengths}
                  </ReferenceField>
                ) : null}
                {r.response_concerns ? (
                  <ReferenceField label="Concerns" full>
                    {r.response_concerns}
                  </ReferenceField>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function ReferenceField({
  label,
  full,
  children,
}: {
  label: string;
  full?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={full ? "col-span-2" : undefined}>
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-0.5 text-foreground">{children}</p>
    </div>
  );
}

/** Link to the induction pack, addressed by the step that produced it. */
function InductionLink({ data, step }: { data: RunDetail; step: RunStep }) {
  const url = data.documents.find((d) => d.kind === step.step_key)?.signed_url;
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
    >
      Open {step.label} PDF <ExternalLink className="h-3 w-3" />
    </a>
  );
}
