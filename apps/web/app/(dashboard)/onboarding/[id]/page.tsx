"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  CheckCircle2,
  ExternalLink,
  FileSignature,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill, type PillTone } from "@/components/actual/kit";
import { LOIDraftEditor } from "@/components/onboarding/loi-draft-editor";
import {
  StageBoard,
  panelStageGroup,
} from "@/components/onboarding/stage-board";
import { SubmissionsPanel } from "@/components/onboarding/submissions-panel";
import {
  builtInPanelFor,
  type RunStep,
} from "@/components/onboarding/step-panel";
import { DOCUMENT_KIND_LABEL as DOC_LABEL } from "@/lib/onboarding-documents";

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
  awaiting_candidate_documents: "Awaiting candidate documents",
};

/** How a signer role reads in the routing summary. */
const SIGNER_LABEL: Record<string, string> = {
  hr: "HR",
  candidate: "candidate",
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
  const loiFileInput = useRef<HTMLInputElement>(null);
  const loiDraftInput = useRef<HTMLInputElement>(null);

  async function uploadSignedLOI(file: File) {
    setBusy("upload-loi");
    setActionError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `/api/onboarding/runs/${id}/loi/upload-signed`,
        { method: "POST", body: form },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't upload the signed PDF.",
        );
        return;
      }
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function replaceLOIDraft(file: File) {
    setBusy("replace-loi-draft");
    setActionError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `/api/onboarding/runs/${id}/loi/replace-draft`,
        { method: "POST", body: form },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't upload the edited .docx.",
        );
        return;
      }
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function approveLOIDraft() {
    if (
      !confirm(
        "Send this LOI to HR for signature? You won't be able to edit further once sent.",
      )
    ) {
      return;
    }
    setBusy("approve-loi-draft");
    setActionError(null);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${id}/loi/approve-draft`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't send the LOI for signature.",
        );
        return;
      }
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function downloadLOIDocx() {
    setBusy("download-loi-docx");
    setActionError(null);
    try {
      const res = await fetch(`/api/onboarding/runs/${id}/loi/docx-url`);
      if (!res.ok) {
        setActionError("Couldn't get a download link. Try again.");
        return;
      }
      const body = (await res.json()) as { docx_url?: string };
      if (body.docx_url) window.open(body.docx_url, "_blank");
    } finally {
      setBusy(null);
    }
  }

  async function openLOISigningLink() {
    setBusy("loi-signing");
    setActionError(null);
    try {
      const res = await fetch(`/api/onboarding/runs/${id}/loi/signing-url`);
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        // 409 means the backend reconciled and found HR already signed —
        // refresh so the UI picks up the updated per-signer statuses.
        if (res.status === 409) {
          await mutate();
        }
        setActionError(
          body.detail
            || body.message
            || "Couldn't open the signing link.",
        );
        return;
      }
      const body = (await res.json()) as { signing_url?: string };
      if (body.signing_url) {
        window.open(body.signing_url, "_blank");
      }
    } finally {
      setBusy(null);
    }
  }

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

  async function approveBundle() {
    setBusy("approve");
    setActionError(null);
    try {
      const res = await fetch(
        `/api/onboarding/runs/${id}/offer-bundle/approve`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setActionError(
          body.detail || body.message || "Couldn't approve the bundle.",
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
  // beyond the built-ins answers null and shows no panel.
  const currentGroup = panelStageGroup(steps);
  const currentStep = currentGroup?.[0] ?? null;
  const panel = builtInPanelFor(currentStep);
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

      {/* Not tied to a stage. Documents can be collected at whatever point the
          org placed the step, and reviewing them deliberately doesn't gate the
          run — so this stays visible wherever the pipeline has got to. Renders
          nothing until the candidate has actually filed something. */}
      <div className="mb-8">
        <SubmissionsPanel runId={data.id} />
      </div>

      {/* Only the step the run is actually sitting in, and only when it is one
          of the built-ins these panels were written for. The rest of the
          pipeline is in the board above — an empty panel for a step nobody has
          reached yet is noise, not information. */}
      {panel === "loi" && currentStep ? (
        <>
          <SectionHeader
            icon={FileSignature}
            title={currentStep.bundle_label ?? currentStep.label}
          />
          <div className="mb-12 rounded-2xl border border-border bg-card p-4">
            <LOIPanel
              data={data}
              step={currentStep}
              busy={busy}
              fileRef={loiFileInput}
              draftFileRef={loiDraftInput}
              onUpload={uploadSignedLOI}
              onReplaceDraft={replaceLOIDraft}
              onApproveDraft={approveLOIDraft}
              onDownloadDocx={downloadLOIDocx}
              onDraftSaved={mutate}
              onOpenSigningLink={openLOISigningLink}
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

      {panel === "appointment" && currentStep ? (
        <>
          <SectionHeader
            icon={FileText}
            title={currentStep.bundle_label ?? currentStep.label}
          />
          <div className="mb-12 rounded-2xl border border-border bg-card p-4">
            <BundlePanel
              data={data}
              steps={currentGroup ?? []}
              busy={busy}
              onApprove={approveBundle}
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

function LOIPanel({
  data,
  step,
  busy,
  fileRef,
  draftFileRef,
  onUpload,
  onReplaceDraft,
  onApproveDraft,
  onDownloadDocx,
  onDraftSaved,
  onOpenSigningLink,
}: {
  data: RunDetail;
  step: RunStep;
  busy: string | null;
  fileRef: React.RefObject<HTMLInputElement | null>;
  draftFileRef: React.RefObject<HTMLInputElement | null>;
  onUpload: (f: File) => void;
  onReplaceDraft: (f: File) => void;
  onApproveDraft: () => void;
  onDownloadDocx: () => void;
  onDraftSaved: () => Promise<unknown>;
  onOpenSigningLink: () => void;
}) {
  const [editing, setEditing] = useState(false);

  // Everything here is addressed by the step, not by the literal key `loi`.
  // The document row's `kind` is the step key the org chose, and the run status
  // only spells `loi_pending_hr_review` while that key happens to be `loi` —
  // for any other name it reads `step_pending_hr_review`, which used to hide
  // this whole panel behind a document that appeared never to have generated.
  const loi = data.documents.find((d) => d.kind === step.step_key);
  const inReview = step.status === "pending_hr_review";

  // One step status covers both ways of getting signed. An envelope on the
  // document is what tells them apart: with one, signing happens in the
  // browser; without one, HR prints, signs and uploads the scan.
  const hasEnvelope = Boolean(loi?.esign_envelope_id);
  const awaitingSign = step.status === "pending_signature" && !hasEnvelope;
  const inEsign = step.status === "pending_signature" && hasEnvelope;

  // What HR calls this document. The step's own name, falling back to the
  // built-in label for a run whose steps predate the catalog.
  const label = step.bundle_label ?? step.label ?? DOC_LABEL.loi;

  // Per-signer progress from the signing envelope, in the routing order the
  // step defines. `esign_signers` is authoritative once an envelope exists;
  // before that the step's roles are all there is to show.
  const signerRoles = step.signer_roles.length
    ? step.signer_roles
    : (loi?.esign_signers ?? []).map((s) => s.role);
  const signers = signerRoles.map((role) => {
    const fromEnvelope = loi?.esign_signers?.find((s) => s.role === role);
    return {
      role,
      name:
        role === "candidate"
          ? data.candidate_name || "Candidate"
          : fromEnvelope?.name || "You (HR)",
      signed: fromEnvelope?.status === "completed",
    };
  });
  const hrSigned = signers.find((s) => s.role === "hr")?.signed ?? false;
  const firstUnsigned = signers.find((s) => !s.signed);
  const myTurn = firstUnsigned?.role === "hr";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">{label}</span>
          {loi ? (
            <span className="ml-2 text-xs text-muted-foreground">
              {loi.sign_status.replace(/_/g, " ")}
              {loi.hr_edit_revision > 0 ? (
                <span className="ml-2 rounded-full bg-amber-tint px-2 py-0.5 text-[10px] font-bold text-amber">
                  edited (rev {loi.hr_edit_revision})
                </span>
              ) : null}
            </span>
          ) : (
            <span className="ml-2 text-xs text-muted-foreground">
              Not generated yet
            </span>
          )}
        </p>
      </div>

      {inReview ? (
        <div className="space-y-3 rounded-xl border border-amber/30 bg-amber-tint p-4">
          <div>
            <p className="text-sm font-medium">
              {editing
                ? "Editing the LOI"
                : "Review the LOI before sending for signature"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {editing ? (
                <>
                  Change any line below and save — the PDF re-renders straight
                  away. Lines you don&rsquo;t touch keep their original
                  formatting.
                </>
              ) : (
                <>
                  When you click <em>Send for signature</em>, an email
                  goes to you with the LOI to print, sign, and scan back.
                </>
              )}
            </p>
          </div>

          {editing ? (
            <LOIDraftEditor
              runId={data.id}
              onSaved={onDraftSaved}
              onClose={() => setEditing(false)}
            />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {loi?.signed_url ? (
                  <Button variant="outline" size="sm" asChild>
                    <a
                      href={loi.signed_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                      Preview LOI in new tab
                    </a>
                  </Button>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Generating the preview…
                  </p>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="mr-1.5 h-3.5 w-3.5" />
                  Edit LOI here
                </Button>
                <Button
                  size="sm"
                  onClick={onApproveDraft}
                  disabled={busy === "approve-loi-draft"}
                >
                  {busy === "approve-loi-draft" ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : null}
                  Send for signature
                </Button>
              </div>

              {/* The Word round-trip stays for edits the text editor can't
                  express — new clauses, tables, layout. */}
              <div className="flex flex-wrap items-center gap-3 border-t border-amber/20 pt-2.5">
                <input
                  ref={draftFileRef}
                  type="file"
                  accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) onReplaceDraft(f);
                    if (draftFileRef.current) draftFileRef.current.value = "";
                  }}
                />
                <p className="text-[11px] text-muted-foreground">
                  Need to restructure it in Word?
                </p>
                <button
                  type="button"
                  onClick={onDownloadDocx}
                  disabled={busy === "download-loi-docx"}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-foreground underline hover:no-underline disabled:opacity-60"
                >
                  {busy === "download-loi-docx" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : null}
                  Download .docx
                </button>
                <button
                  type="button"
                  onClick={() => draftFileRef.current?.click()}
                  disabled={busy === "replace-loi-draft"}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-foreground underline hover:no-underline disabled:opacity-60"
                >
                  {busy === "replace-loi-draft" ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Upload className="h-3 w-3" />
                  )}
                  Upload an edited .docx
                </button>
              </div>
            </>
          )}
        </div>
      ) : null}

      {awaitingSign ? (
        <div className="rounded-xl border border-dashed border-border bg-muted/40 p-4">
          <p className="text-sm">
            Print the draft, sign it, scan, then upload the signed PDF here.
          </p>
          <div className="mt-3 flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onUpload(f);
              }}
            />
            <Button
              size="sm"
              onClick={() => fileRef.current?.click()}
              disabled={busy === "upload-loi"}
            >
              {busy === "upload-loi" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="mr-1.5 h-3.5 w-3.5" />
              )}
              Upload signed PDF
            </Button>
          </div>
        </div>
      ) : null}

      {inEsign ? (
        <div className="space-y-3 rounded-xl border border-brand/30 bg-brand-tint p-4">
          <div>
            <p className="text-sm font-medium">Signing {label}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {!firstUnsigned ? (
                <>Everyone has signed. Sending it on.</>
              ) : hrSigned || !signerRoles.includes("hr") ? (
                <>
                  Waiting on{" "}
                  <strong>{firstUnsigned.name}</strong>. They&apos;ve been
                  emailed a signing link.
                </>
              ) : (
                <>
                  The envelope is routed{" "}
                  <strong>
                    {signers.map((s) => SIGNER_LABEL[s.role] ?? s.role).join(" → ")}
                  </strong>
                  . Each signer is emailed automatically when it reaches them.
                </>
              )}
            </p>
          </div>

          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            {signers.map((signer, i) => (
              <div
                key={signer.role}
                className={`flex items-center justify-between rounded-lg border px-3 py-2 ${
                  signer.signed
                    ? "border-success/30 bg-success-tint"
                    : "border-border bg-muted/40"
                }`}
              >
                <dt className="font-medium text-foreground">{signer.name}</dt>
                <dd
                  className={
                    signer.signed
                      ? "font-medium text-success-ink"
                      : "text-muted-foreground"
                  }
                >
                  {signer.signed
                    ? "Signed ✓"
                    : signer.role === firstUnsigned?.role
                      ? "Pending"
                      : `Waiting for ${signers[i - 1]?.name ?? "the previous signer"}`}
                </dd>
              </div>
            ))}
          </dl>

          {myTurn ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={onOpenSigningLink}
                disabled={busy === "loi-signing"}
              >
                {busy === "loi-signing" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                )}
                Open my signing link
              </Button>
              <p className="text-[11px] text-muted-foreground">
                Link expires after 5 minutes — click again for a fresh one.
              </p>
            </div>
          ) : null}
        </div>
      ) : null}

      {loi?.signed_pdf_path ? (
        <p className="text-xs text-muted-foreground">
          Signed copy uploaded {relativeTime(loi.signed_uploaded_at)}.
        </p>
      ) : null}
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

function BundlePanel({
  data,
  steps,
  busy,
  onApprove,
}: {
  data: RunDetail;
  /** Every document in the bundle, in the order the org arranged them. */
  steps: RunStep[];
  busy: string | null;
  onApprove: () => void;
}) {
  // One card per member rather than a fixed appointment-letter-and-NDA pair —
  // a bundle is whatever documents the org grouped, and each is addressed by
  // its step key because that is what the document row's `kind` holds.
  const docs = steps.map((step) => ({
    key: step.id,
    label: step.label,
    doc: data.documents.find((d) => d.kind === step.step_key),
  }));
  const awaitingApproval = steps[0]?.status === "pending_hr_review";

  return (
    <div className="space-y-3" id="offer-bundle">
      <div className="grid gap-2 sm:grid-cols-2">
        {docs.map((d) => (
          <DocCard key={d.key} label={d.label} doc={d.doc} />
        ))}
      </div>

      {awaitingApproval ? (
        <div className="rounded-xl border border-dashed border-border bg-muted/40 p-4">
          <p className="text-sm">
            Review both documents above. When you&apos;re ready, send the
            bundle to the candidate.
          </p>
          <Button
            size="sm"
            className="mt-3"
            onClick={onApprove}
            disabled={busy === "approve"}
          >
            {busy === "approve" ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
            )}
            Approve and send to candidate
          </Button>
        </div>
      ) : null}

      {data.appointment_sent_at ? (
        <p className="text-xs text-muted-foreground">
          Sent to candidate {relativeTime(data.appointment_sent_at)}.
        </p>
      ) : null}
    </div>
  );
}

function DocCard({
  label,
  doc,
}: {
  label: string;
  doc: DocumentRow | undefined;
}) {
  return (
    <div className="rounded-xl border border-border bg-muted/40 p-3">
      <p className="text-xs font-bold text-foreground">{label}</p>
      {doc ? (
        <>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {doc.sign_status.replace(/_/g, " ")}
            {doc.esign_status ? (
              <span className="ml-1 rounded-full bg-brand-tint px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-brand">
                Signing: {doc.esign_status}
              </span>
            ) : null}
          </p>
          {doc.signed_url ? (
            <a
              href={doc.signed_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
            >
              Open PDF <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
          {doc.esign_signing_url && doc.esign_status !== "completed" ? (
            <a
              href={doc.esign_signing_url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-2 mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-brand underline hover:no-underline"
            >
              Open signing link <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </>
      ) : (
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          Not generated yet
        </p>
      )}
    </div>
  );
}
