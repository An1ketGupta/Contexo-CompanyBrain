"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cloud,
  Loader2,
  Upload,
  Users,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, StatusPill, type PillTone } from "@/components/actual/kit";
import {
  OnboardingSources,
  type SourcesResponse,
} from "@/components/onboarding/onboarding-sources";
import { TemplateMapperModal } from "@/components/onboarding/template-mapper-modal";
import { openDriveFilePicker } from "@/components/integrations/google-drive-picker";

interface OnboardingRunRow {
  id: string;
  candidate_name: string;
  candidate_email: string;
  role_title: string;
  status: string;
  blocked_reason: string | null;
  blocked_template_kind: string | null;
  current_step: string | null;
  start_date: string;
  created_at: string;
  updated_at: string;
  loi_sent_to_hr_at: string | null;
  bgv_completed_at: string | null;
  completed_at: string | null;
}

interface ListResponse {
  runs: OnboardingRunRow[];
}

interface TemplateStatusRow {
  id: string;
  name: string;
  template_kind: string;
}

interface TemplateStatusResponse {
  loi: TemplateStatusRow | null;
  appointment_letter: TemplateStatusRow | null;
  nda: TemplateStatusRow | null;
  induction: TemplateStatusRow | null;
}

interface IntegrationsStatusResponse {
  drive?: {
    available?: boolean;
    connected?: boolean;
  };
}

type StatusKey = "all" | "active" | "blocked" | "completed";

const TEMPLATE_KINDS = [
  { key: "loi" as const, label: "Letter of Intent" },
  { key: "appointment_letter" as const, label: "Appointment Letter" },
  { key: "nda" as const, label: "NDA" },
  { key: "induction" as const, label: "Induction" },
];

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  loi_generating: "Preparing LOI from template",
  loi_pending_hr_sign: "Awaiting HR signature",
  loi_signed_uploaded: "LOI signed — sending",
  loi_sent_to_candidate: "LOI sent",
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

const TERMINAL = new Set(["completed", "cancelled", "failed"]);
const BLOCKED = new Set(["blocked_missing_template", "failed"]);

function statusStyle(status: string): { tone: PillTone; stripe: string } {
  if (status === "completed") return { tone: "green", stripe: "bg-success" };
  if (BLOCKED.has(status)) return { tone: "red", stripe: "bg-destructive" };
  if (status === "cancelled") return { tone: "gray", stripe: "bg-border" };
  return { tone: "blue", stripe: "bg-brand" };
}

function relativeTime(iso: string): string {
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

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function OnboardingListPage() {
  const [filter, setFilter] = useState<StatusKey>("all");
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [tagError, setTagError] = useState<string | null>(null);
  const [mapper, setMapper] = useState<{ docId: string; kind: string } | null>(
    null,
  );

  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/onboarding/runs",
    fetcher,
    { refreshInterval: 15_000 },
  );
  const { data: templates, mutate: refreshTemplates } =
    useSWR<TemplateStatusResponse>("/api/onboarding/templates/status", fetcher);
  const { data: integrations } = useSWR<IntegrationsStatusResponse>(
    "/api/integrations/status",
    fetcher,
  );
  const driveConnected = Boolean(integrations?.drive?.connected);
  const {
    data: sources,
    error: sourcesError,
    isLoading: sourcesLoading,
  } = useSWR<SourcesResponse>("/api/onboarding/sources", fetcher, {
    refreshInterval: 60_000,
  });

  const rows = data?.runs ?? [];
  const filtered = useMemo(() => {
    if (filter === "all") return rows;
    if (filter === "completed")
      return rows.filter((r) => r.status === "completed");
    if (filter === "blocked") return rows.filter((r) => BLOCKED.has(r.status));
    return rows.filter((r) => !TERMINAL.has(r.status));
  }, [rows, filter]);

  const counts = useMemo(
    () => ({
      all: rows.length,
      active: rows.filter((r) => !TERMINAL.has(r.status)).length,
      blocked: rows.filter((r) => BLOCKED.has(r.status)).length,
      completed: rows.filter((r) => r.status === "completed").length,
    }),
    [rows],
  );

const missingCount = templates
    ? TEMPLATE_KINDS.filter((k) => !templates[k.key]).length
    : 0;

  const allConfigured = missingCount === 0 && templates !== undefined;

  async function tagDoc(docId: string, kind: string) {
    setBusy(`${kind}`);
    setTagError(null);
    try {
      const res = await fetch("/api/onboarding/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_id: docId, template_kind: kind }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as {
          detail?: string;
          message?: string;
        };
        setTagError(body.detail || body.message || "Couldn't assign template.");
        return;
      }
      await refreshTemplates();
      // Auto-open AI mapper. If the DOCX already has placeholders, the
      // modal short-circuits and shows a "no conversion needed" state.
      setMapper({ docId, kind });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-8">
        <PageHeader
          eyebrow="Talent"
          title="Onboarding"
          description="LOI → BGV → Appointment + NDA → Policies → Induction — driven by the Onboarding agent."
        />
      </div>

      {/* ── Templates setup ─────────────────────────────────────── */}
      <div className="mb-6 rounded-2xl border border-border bg-card">
        <button
          onClick={() => setTemplatesOpen((o) => !o)}
          className="flex w-full items-center justify-between px-5 py-4 text-left"
        >
          <div className="flex items-center gap-2">
            {allConfigured ? (
              <CheckCircle2 className="h-4 w-4 text-success" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-amber" />
            )}
            <span className="text-sm font-bold text-foreground">
              Document templates
            </span>
            {!allConfigured && missingCount > 0 ? (
              <span className="rounded-full bg-amber-tint px-2 py-0.5 text-[10px] font-bold text-amber">
                {missingCount} missing
              </span>
            ) : allConfigured ? (
              <span className="rounded-full bg-success-tint px-2 py-0.5 text-[10px] font-bold text-success">
                All set
              </span>
            ) : null}
          </div>
          {templatesOpen ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </button>

        {templatesOpen ? (
          <div className="border-t border-border px-5 pb-5 pt-4">
            {tagError ? (
              <div className="mb-3 rounded-xl border border-destructive/30 bg-destructive-soft p-2.5 text-xs font-medium text-destructive">
                {tagError}
              </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-2">
              {TEMPLATE_KINDS.map(({ key, label }) => {
                const current = templates?.[key] ?? null;
                return (
                  <TemplateSlot
                    key={key}
                    kind={key}
                    label={label}
                    current={current}
                    onAssign={(docId) => tagDoc(docId, key)}
                    onDriveImported={() => {
                      void refreshTemplates();
                    }}
                    isBusy={busy === key}
                    driveConnected={driveConnected}
                  />
                );
              })}
            </div>
          </div>
        ) : null}
      </div>

      {/* ── Runs list ──────────────────────────────────────────── */}
      <h2 className="mb-3 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        Onboarding runs
      </h2>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(
          [
            { key: "all", label: "All" },
            { key: "active", label: "Active" },
            { key: "blocked", label: "Blocked" },
            { key: "completed", label: "Completed" },
          ] as const
        ).map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={
              "rounded-full border px-3 py-1 text-xs font-semibold transition-colors " +
              (filter === f.key
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border text-muted-foreground hover:bg-muted hover:text-foreground")
            }
          >
            {f.label}
            <span className={filter === f.key ? "ml-1.5 opacity-70" : "ml-1.5 text-muted-foreground"}>
              {counts[f.key]}
            </span>
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full rounded-2xl" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-destructive/30 bg-destructive-soft p-6 text-sm font-medium text-destructive">
          Couldn&apos;t load runs. {String(error)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-background px-6 py-12 text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
            <Users className="h-5 w-5" />
          </div>
          <p className="text-sm font-bold text-foreground">
            {filter === "all" ? "No onboarding runs yet" : `No ${filter} runs`}
          </p>
          <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
            {filter === "all"
              ? "Pick a candidate from the section above, or add one manually."
              : "Switch filter to see runs in other states."}
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {filtered.map((r) => {
            const meta = statusStyle(r.status);
            return (
              <li key={r.id}>
                <Link
                  href={`/onboarding/${r.id}`}
                  className="group flex items-stretch gap-4 rounded-2xl border border-border bg-card p-4 transition-colors hover:bg-muted/40"
                >
                  <div className={"w-1 shrink-0 rounded-full " + meta.stripe} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-bold text-foreground">
                        {r.candidate_name}
                      </p>
                      <StatusPill tone={meta.tone}>
                        {STATUS_LABELS[r.status] || r.status}
                      </StatusPill>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {r.role_title} · starts {r.start_date}
                    </p>
                    {r.blocked_reason ? (
                      <p className="mt-1 truncate text-xs font-medium text-destructive">
                        {r.blocked_reason}
                      </p>
                    ) : null}
                  </div>
                  <div className="hidden text-right text-xs text-muted-foreground sm:block">
                    Updated {relativeTime(r.updated_at)}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      <OnboardingSources
        sources={sources}
        isLoading={sourcesLoading}
        error={sourcesError}
      />

      <TemplateMapperModal
        documentId={mapper?.docId ?? null}
        templateKind={mapper?.kind ?? "loi"}
        open={mapper !== null}
        onOpenChange={(o) => {
          if (!o) setMapper(null);
        }}
        onApplied={() => {
          void refreshTemplates();
        }}
      />
    </div>
  );
}

function TemplateSlot({
  kind,
  label,
  current,
  onAssign,
  onDriveImported,
  isBusy,
  driveConnected,
}: {
  kind: string;
  label: string;
  current: TemplateStatusRow | null;
  onAssign: (docId: string) => void;
  onDriveImported: () => void;
  isBusy: boolean;
  driveConnected: boolean;
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
    </div>
  );
}
