"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileSignature,
  FileText,
  Loader2,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill, type PillTone } from "@/components/actual/kit";

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
  failed: "Failed",
  cancelled: "Cancelled",
};

const DOC_LABEL: Record<string, string> = {
  loi: "Letter of Intent",
  appointment_letter: "Appointment Letter",
  nda: "NDA",
  induction: "Induction document",
  offer_bundle: "Offer bundle",
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

  async function uploadSignedLoi(file: File) {
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

  async function replaceLoiDraft(file: File) {
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

  async function approveLoiDraft() {
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

  async function downloadLoiDocx() {
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

  async function openLoiSigningLink() {
    setBusy("loi-signing");
    setActionError(null);
    try {
      const res = await fetch(`/api/onboarding/runs/${id}/loi/signing-url`);
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
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
                Upload your{" "}
                {(data.blocked_template_kind || "")
                  .replace(/_/g, " ")
                  .toUpperCase()}{" "}
                template to continue
              </p>
              <Link
                href="/onboarding/templates"
                className="mt-2 inline-block text-xs font-bold text-amber underline hover:no-underline"
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

      {/* LOI section */}
      <SectionHeader icon={FileSignature} title="Letter of Intent" />
      <div className="mb-6 rounded-2xl border border-border bg-card p-4">
        <LoiPanel
          data={data}
          busy={busy}
          fileRef={loiFileInput}
          draftFileRef={loiDraftInput}
          onUpload={uploadSignedLoi}
          onReplaceDraft={replaceLoiDraft}
          onApproveDraft={approveLoiDraft}
          onDownloadDocx={downloadLoiDocx}
          onOpenSigningLink={openLoiSigningLink}
        />
      </div>

      {/* BGV section */}
      <SectionHeader icon={ShieldCheck} title="Background verification" />
      <div className="mb-6 rounded-2xl border border-border bg-card p-4">
        <BgvPanel
          data={data}
          references={data.references}
          busy={busy}
          onHrOverride={submitHrReferencesOverride}
          onNudge={nudgeCandidate}
          onExtendToken={extendReferencesToken}
        />
      </div>

      {/* Appointment + NDA section */}
      <SectionHeader icon={FileText} title="Appointment Letter + NDA" />
      <div className="mb-6 rounded-2xl border border-border bg-card p-4">
        <BundlePanel
          data={data}
          busy={busy}
          onApprove={approveBundle}
        />
      </div>

      {/* Policies + Induction */}
      <SectionHeader icon={CheckCircle2} title="Policies & Induction" />
      <div className="mb-6 grid gap-3 sm:grid-cols-2">
        <div className="rounded-2xl border border-border bg-card p-4">
          <p className="text-sm font-medium">Policies</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Assigned: {relativeTime(data.policies_assigned_at)}
            <br />
            Acknowledged: {relativeTime(data.policies_acknowledged_at)}
          </p>
        </div>
        <div className="rounded-2xl border border-border bg-card p-4">
          <p className="text-sm font-medium">Induction</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Sent: {relativeTime(data.induction_sent_at)}
          </p>
          {data.documents.find((d) => d.kind === "induction")?.signed_url ? (
            <a
              href={
                data.documents.find((d) => d.kind === "induction")!.signed_url!
              }
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
            >
              Open induction PDF <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      </div>

      {/* Timeline */}
      <SectionHeader icon={Clock} title="Timeline" />
      <div className="mb-12 rounded-2xl border border-border bg-card p-4">
        <ol className="space-y-3">
          {data.events.map((e) => (
            <li key={e.id} className="flex gap-3 text-xs">
              <span className="mt-0.5 text-muted-foreground">
                {relativeTime(e.created_at)}
              </span>
              <span className="text-foreground">
                <span className="mr-1.5 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase text-muted-foreground">
                  {e.actor_kind}
                </span>
                {e.message || e.event_type}
              </span>
            </li>
          ))}
          {data.events.length === 0 ? (
            <li className="text-xs text-muted-foreground">No events yet.</li>
          ) : null}
        </ol>
      </div>
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

function LoiPanel({
  data,
  busy,
  fileRef,
  draftFileRef,
  onUpload,
  onReplaceDraft,
  onApproveDraft,
  onDownloadDocx,
  onOpenSigningLink,
}: {
  data: RunDetail;
  busy: string | null;
  fileRef: React.RefObject<HTMLInputElement | null>;
  draftFileRef: React.RefObject<HTMLInputElement | null>;
  onUpload: (f: File) => void;
  onReplaceDraft: (f: File) => void;
  onApproveDraft: () => void;
  onDownloadDocx: () => void;
  onOpenSigningLink: () => void;
}) {
  const loi = data.documents.find((d) => d.kind === "loi");
  const inReview = data.status === "loi_pending_hr_review";
  const awaitingSign = data.status === "loi_pending_hr_sign";
  const inEsign = data.status === "loi_pending_esign_signature";
  const esignStatus = (loi?.esign_status || "").toLowerCase();

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">{DOC_LABEL.loi}</span>
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
        <div className="flex items-center gap-2">
          {loi?.signed_url ? (
            <a
              href={loi.signed_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
            >
              Open PDF <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      </div>

      {inReview ? (
        <div className="space-y-3 rounded-xl border border-amber/30 bg-amber-tint p-4">
          <div>
            <p className="text-sm font-medium">
              Review the LOI before sending for signature
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Check the filled values are correct. Download the .docx if you
              need to tweak wording in Word, then re-upload here. When you
              click <em>Send for signature</em>, an email goes to you with the
              LOI to print, sign, and scan back.
            </p>
          </div>

          {loi?.signed_url ? (
            <a
              href={loi.signed_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 self-start rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Preview LOI in new tab
            </a>
          ) : (
            <p className="text-xs text-muted-foreground">
              Generating the preview…
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
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
            <Button
              variant="outline"
              size="sm"
              onClick={onDownloadDocx}
              disabled={busy === "download-loi-docx"}
            >
              {busy === "download-loi-docx" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : null}
              Download .docx to edit
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => draftFileRef.current?.click()}
              disabled={busy === "replace-loi-draft"}
            >
              {busy === "replace-loi-draft" ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="mr-1.5 h-3.5 w-3.5" />
              )}
              Replace with edited .docx
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
            <p className="text-sm font-medium">Signing the LOI</p>
            <p className="mt-1 text-xs text-muted-foreground">
              The envelope is routed <strong>HR → candidate</strong>. You sign
              first. The candidate will receive a signing email automatically
              once you're done.
            </p>
          </div>

          <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
            <div className="flex items-center justify-between rounded-lg border border-border bg-muted/40 px-3 py-2">
              <dt className="font-medium text-foreground">You (HR)</dt>
              <dd className="text-muted-foreground">
                {esignStatus === "completed" ? "Signed ✓" : "Pending"}
              </dd>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border bg-muted/40 px-3 py-2">
              <dt className="font-medium text-foreground">
                {data.candidate_name || "Candidate"}
              </dt>
              <dd className="text-muted-foreground">
                {esignStatus === "completed"
                  ? "Signed ✓"
                  : "Waiting for HR first"}
              </dd>
            </div>
          </dl>

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
  const awaitingCandidate = data.status === "awaiting_candidate_references";

  const formExpired =
    data.references_form_expires_at != null &&
    new Date(data.references_form_expires_at) < new Date();

  const formExpiryLabel = data.references_form_expires_at
    ? `Form link ${formExpired ? "expired" : "expires"} ${relativeTime(data.references_form_expires_at)}`
    : null;

  if (awaitingCandidate && references.length === 0) {
    return (
      <div className="space-y-3">
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
              "text-xs " +
              (formExpired
                ? "font-medium text-destructive"
                : "text-muted-foreground")
            }
          >
            {formExpiryLabel}
          </p>
        ) : null}

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

        <HrReferencesOverride busy={busy} onSubmit={onHrOverride} />
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
        return (
          <li
            key={r.id}
            className="flex items-start justify-between gap-3 rounded-xl border border-border bg-muted/40 p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">
                {r.reference_name}{" "}
                <span className="font-normal text-muted-foreground">
                  ({r.reference_email})
                </span>
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
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
                <div className="mt-2 space-y-1 rounded-lg border border-border bg-card p-2 text-xs">
                  <p>
                    <strong>Worked together:</strong>{" "}
                    {r.response_worked_together_months ?? "—"} months ·{" "}
                    <strong>Would recommend:</strong>{" "}
                    {r.response_would_recommend === null
                      ? "—"
                      : r.response_would_recommend
                        ? "Yes"
                        : "No"}
                  </p>
                  {r.response_role_description ? (
                    <p>
                      <strong>Role:</strong> {r.response_role_description}
                    </p>
                  ) : null}
                  {r.response_strengths ? (
                    <p>
                      <strong>Strengths:</strong> {r.response_strengths}
                    </p>
                  ) : null}
                  {r.response_concerns ? (
                    <p>
                      <strong>Concerns:</strong> {r.response_concerns}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
            <span
              className={
                "rounded-full px-2.5 py-0.5 text-[10px] font-bold " +
                (submitted
                  ? "bg-success-tint text-success"
                  : "bg-brand-tint text-brand")
              }
            >
              {r.status}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function BundlePanel({
  data,
  busy,
  onApprove,
}: {
  data: RunDetail;
  busy: string | null;
  onApprove: () => void;
}) {
  const al = data.documents.find((d) => d.kind === "appointment_letter");
  const nda = data.documents.find((d) => d.kind === "nda");
  const awaitingApproval = data.status === "appointment_pending_hr_review";

  return (
    <div className="space-y-3" id="offer-bundle">
      <div className="grid gap-2 sm:grid-cols-2">
        <DocCard label={DOC_LABEL.appointment_letter} doc={al} />
        <DocCard label={DOC_LABEL.nda} doc={nda} />
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
