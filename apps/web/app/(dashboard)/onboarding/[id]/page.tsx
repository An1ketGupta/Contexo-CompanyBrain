"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileSignature,
  FileText,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Upload,
  Users,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

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
  docusign_envelope_id: string | null;
  docusign_status: string | null;
  docusign_signing_url: string | null;
  docusign_completed_at: string | null;
  used_default_template: boolean;
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
  created_at: string;
  updated_at: string;
  references: ReferenceRow[];
  documents: DocumentRow[];
  events: EventRow[];
}

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  loi_generating: "Generating LOI",
  loi_pending_hr_sign: "Awaiting HR signature",
  loi_signed_uploaded: "LOI signed — sending",
  loi_sent_to_candidate: "LOI sent",
  bgv_pending: "BGV in progress",
  bgv_complete: "BGV complete",
  appointment_bundle_generating: "Generating Appointment Letter + NDA",
  appointment_pending_hr_review: "Awaiting HR approval",
  appointment_sent_to_candidate: "Appointment + NDA sent",
  policies_assigned: "Policies assigned",
  policies_acknowledged: "Policies acknowledged",
  induction_generating: "Generating induction",
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
      await fetch(`/api/onboarding/runs/${id}/resume`, { method: "POST" });
      // Give the agent a beat to update state before we re-pull.
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
        <p className="text-sm text-red-600">
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
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to onboarding
      </Link>

      <header className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              {data.candidate_name}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
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

        <div className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-medium">
          <span
            className={
              "h-1.5 w-1.5 rounded-full " +
              (data.status === "completed"
                ? "bg-emerald-500"
                : isBlocked || data.status === "failed"
                  ? "bg-red-500"
                  : "bg-blue-500")
            }
          />
          {STATUS_LABELS[data.status] || data.status}
        </div>
      </header>

      {actionError ? (
        <div className="mb-4 rounded-md border border-red-300/60 bg-red-50 p-3 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          {actionError}
        </div>
      ) : null}

      {isBlocked ? (
        <div className="mb-6 rounded-lg border border-amber-300/60 bg-amber-50 p-4 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
                Upload your{" "}
                {(data.blocked_template_kind || "")
                  .replace(/_/g, " ")
                  .toUpperCase()}{" "}
                template to continue
              </p>
              <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
                The agent paused because no template is tagged for this kind.
                Upload the DOCX to the knowledge base, then tag it from the
                document page — the agent will resume automatically.
              </p>
              <Link
                href="/onboarding/templates"
                className="mt-2 inline-block text-xs font-medium text-amber-900 underline hover:no-underline dark:text-amber-50"
              >
                Open templates →
              </Link>
            </div>
          </div>
        </div>
      ) : null}

      {/* Summary card */}
      <section className="mb-8 grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-border bg-background p-4 sm:grid-cols-4">
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
      <div className="mb-6 rounded-lg border border-border bg-background p-4">
        <LoiPanel
          data={data}
          busy={busy}
          fileRef={loiFileInput}
          onUpload={uploadSignedLoi}
        />
      </div>

      {/* BGV section */}
      <SectionHeader icon={ShieldCheck} title="Background verification" />
      <div className="mb-6 rounded-lg border border-border bg-background p-4">
        <BgvPanel references={data.references} />
      </div>

      {/* Appointment + NDA section */}
      <SectionHeader icon={FileText} title="Appointment Letter + NDA" />
      <div className="mb-6 rounded-lg border border-border bg-background p-4">
        <BundlePanel
          data={data}
          busy={busy}
          onApprove={approveBundle}
        />
      </div>

      {/* Policies + Induction */}
      <SectionHeader icon={CheckCircle2} title="Policies & Induction" />
      <div className="mb-6 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-border bg-background p-4">
          <p className="text-sm font-medium">Policies</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Assigned: {relativeTime(data.policies_assigned_at)}
            <br />
            Acknowledged: {relativeTime(data.policies_acknowledged_at)}
          </p>
        </div>
        <div className="rounded-lg border border-border bg-background p-4">
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
      <div className="mb-12 rounded-lg border border-border bg-background p-4">
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
    <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      <Icon className="h-3.5 w-3.5" />
      {title}
    </h2>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-0.5 text-sm font-medium text-foreground">{value}</p>
    </div>
  );
}

function LoiPanel({
  data,
  busy,
  fileRef,
  onUpload,
}: {
  data: RunDetail;
  busy: string | null;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onUpload: (f: File) => void;
}) {
  const loi = data.documents.find((d) => d.kind === "loi");
  const awaitingSign = data.status === "loi_pending_hr_sign";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">
          <span className="font-medium">{DOC_LABEL.loi}</span>
          {loi ? (
            <span className="ml-2 text-xs text-muted-foreground">
              {loi.sign_status.replace(/_/g, " ")}
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
              Download draft <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      </div>

      {awaitingSign ? (
        <div className="rounded-md border border-dashed border-border bg-muted/30 p-4">
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

      {loi?.signed_pdf_path ? (
        <p className="text-xs text-muted-foreground">
          Signed copy uploaded {relativeTime(loi.signed_uploaded_at)}.
        </p>
      ) : null}
    </div>
  );
}

function BgvPanel({ references }: { references: ReferenceRow[] }) {
  if (references.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No references on file. Cancel and recreate this run to add some.
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
            className="flex items-start justify-between gap-3 rounded-md border border-border bg-muted/20 p-3"
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
                <div className="mt-2 space-y-1 rounded border border-border bg-background p-2 text-xs">
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
                "rounded-full px-2 py-0.5 text-[10px] font-medium " +
                (submitted
                  ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
                  : "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300")
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
        <div className="rounded-md border border-dashed border-border bg-muted/30 p-4">
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
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <p className="text-xs font-medium text-foreground">{label}</p>
      {doc ? (
        <>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {doc.sign_status.replace(/_/g, " ")}
            {doc.docusign_status ? (
              <span className="ml-1 rounded bg-blue-100 px-1 py-0.5 text-[10px] uppercase tracking-wide text-blue-700 dark:bg-blue-500/20 dark:text-blue-300">
                DocuSign: {doc.docusign_status}
              </span>
            ) : null}
          </p>
          {doc.used_default_template ? (
            <p className="mt-1 text-[10px] italic text-amber-700 dark:text-amber-300">
              Using NirnayaIQ default template — upload your own to customise.
            </p>
          ) : null}
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
          {doc.docusign_signing_url && doc.docusign_status !== "completed" ? (
            <a
              href={doc.docusign_signing_url}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-2 mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-blue-700 underline hover:no-underline dark:text-blue-300"
            >
              Open in DocuSign <ExternalLink className="h-3 w-3" />
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
