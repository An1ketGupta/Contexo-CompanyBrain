"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Users } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import {
  OnboardingSources,
  type SourcesResponse,
} from "@/components/onboarding/onboarding-sources";

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
}


type StatusKey = "all" | "active" | "blocked" | "completed";

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

const TERMINAL = new Set(["completed", "cancelled", "failed"]);
const BLOCKED = new Set(["blocked_missing_template", "failed"]);

function statusStyle(status: string): { dot: string; text: string; bg: string } {
  if (status === "completed")
    return {
      dot: "bg-emerald-500",
      text: "text-emerald-700 dark:text-emerald-300",
      bg: "bg-emerald-50 dark:bg-emerald-500/10",
    };
  if (BLOCKED.has(status))
    return {
      dot: "bg-red-500",
      text: "text-red-700 dark:text-red-300",
      bg: "bg-red-50 dark:bg-red-500/10",
    };
  if (status === "cancelled")
    return {
      dot: "bg-zinc-400",
      text: "text-zinc-700 dark:text-zinc-300",
      bg: "bg-zinc-100 dark:bg-zinc-800",
    };
  return {
    dot: "bg-blue-500",
    text: "text-blue-700 dark:text-blue-300",
    bg: "bg-blue-50 dark:bg-blue-500/10",
  };
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

  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/onboarding/runs",
    fetcher,
    { refreshInterval: 15_000 },
  );
  const { data: templates } = useSWR<TemplateStatusResponse>(
    "/api/onboarding/templates/status",
    fetcher,
  );
  const {
    data: sources,
    error: sourcesError,
    isLoading: sourcesLoading,
  } = useSWR<SourcesResponse>("/api/onboarding/sources", fetcher, {
    // Lower cadence than runs — published job state changes rarely.
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

  const missingTemplates: string[] = [];
  if (templates) {
    if (!templates.loi) missingTemplates.push("LOI");
    if (!templates.appointment_letter)
      missingTemplates.push("Appointment Letter");
    if (!templates.nda) missingTemplates.push("NDA");
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Onboarding
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            LOI → BGV → Appointment + NDA → Policies → Induction — driven by the
            Onboarding agent.
          </p>
        </div>
      </header>

      {missingTemplates.length > 0 ? (
        <div className="mb-6 rounded-lg border border-amber-300/60 bg-amber-50 p-4 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="flex-1">
              <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
                Missing template{missingTemplates.length === 1 ? "" : "s"}:{" "}
                {missingTemplates.join(", ")}
              </p>
              <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
                Upload your DOCX templates to the knowledge base, then tag them
                with the matching kind on the document page. Onboarding runs
                will block until each required template is tagged.
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

      <OnboardingSources
        sources={sources}
        isLoading={sourcesLoading}
        error={sourcesError}
      />

      <h2 className="mb-2 text-sm font-semibold text-foreground">
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
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors " +
              (filter === f.key
                ? "border-foreground bg-foreground text-background"
                : "border-border text-foreground hover:bg-muted")
            }
          >
            {f.label}
            <span className="ml-1.5 text-muted-foreground">
              {counts[f.key]}
            </span>
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-red-300/60 bg-red-50 p-6 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          Couldn&apos;t load runs. {String(error)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 px-6 py-12 text-center">
          <Users className="mb-3 h-10 w-10 text-muted-foreground" />
          <p className="text-sm font-medium text-foreground">
            {filter === "all"
              ? "No onboarding runs yet"
              : `No ${filter} runs`}
          </p>
          <p className="mt-1 max-w-md text-xs text-muted-foreground">
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
                  className="group flex items-stretch gap-4 rounded-lg border border-border bg-background p-4 transition-colors hover:bg-muted/40"
                >
                  <div className={"w-1 shrink-0 rounded-full " + meta.dot} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-foreground">
                        {r.candidate_name}
                      </p>
                      <span
                        className={
                          "rounded-full px-2 py-0.5 text-[10px] font-medium " +
                          meta.bg +
                          " " +
                          meta.text
                        }
                      >
                        {STATUS_LABELS[r.status] || r.status}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {r.role_title} · starts {r.start_date}
                    </p>
                    {r.blocked_reason ? (
                      <p className="mt-1 truncate text-xs text-red-600">
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
    </div>
  );
}
