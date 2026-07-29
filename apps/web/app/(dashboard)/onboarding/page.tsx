"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Users } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, StatusPill, type PillTone } from "@/components/actual/kit";
import {
  OnboardingSources,
  type SourcesResponse,
} from "@/components/onboarding/onboarding-sources";
import { TemplateReadinessPanel } from "@/components/onboarding/template-readiness-panel";

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

type StatusKey = "all" | "active" | "blocked" | "completed";

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

  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/onboarding/runs",
    fetcher,
    { refreshInterval: 15_000 },
  );
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

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-8">
        <PageHeader
          eyebrow="Talent"
          title="Onboarding"
          description="LOI → BGV → Appointment + NDA → Policies → Induction — driven by the Onboarding agent."
        />
      </div>

      <TemplateReadinessPanel
        open={templatesOpen}
        onToggle={() => setTemplatesOpen((o) => !o)}
      />

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

    </div>
  );
}
