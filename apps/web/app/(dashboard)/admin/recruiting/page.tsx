"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/admin/stat-card";

type Period = "7d" | "30d" | "90d";

interface AuditRow {
  id: string;
  requisition_id: string | null;
  action: string;
  status: "success" | "failure" | "skipped";
  ats_platform: string | null;
  status_code: number | null;
  error_message: string | null;
  duration_ms: number | null;
  created_at: string;
}

interface RecruitingAnalytics {
  period: Period;
  stats: {
    total_requisitions: number;
    published: number;
    draft: number;
    failed: number;
    grounded_rate: number;
    median_seconds_to_publish: number | null;
  };
  by_ats: { platform: string; count: number }[];
  daily_series: { day: string; count: number }[];
  top_recruiters: {
    user_id: string;
    name: string;
    email: string | null;
    requisitions: number;
  }[];
  recent_audit: AuditRow[];
}

const PERIOD_OPTIONS: { label: string; value: Period }[] = [
  { label: "7 days", value: "7d" },
  { label: "30 days", value: "30d" },
  { label: "90 days", value: "90d" },
];

const fetcher = async (url: string): Promise<RecruitingAnalytics> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}

export default function AdminRecruitingPage() {
  const [period, setPeriod] = useState<Period>("30d");
  const { data, error, isLoading } = useSWR<RecruitingAnalytics>(
    `/api/admin/recruiting?period=${period}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Recruiting analytics</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Hiring throughput, ATS distribution, and the audit trail for every
            publish action your org has run.
          </p>
        </div>
        <div className="flex gap-1.5">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setPeriod(opt.value)}
              className={
                "rounded-md border px-2.5 py-1 text-xs font-medium transition " +
                (period === opt.value
                  ? "border-zinc-900 bg-zinc-900 text-white"
                  : "border-border bg-background text-muted-foreground hover:border-zinc-400")
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
      </header>

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error.message}
        </div>
      ) : isLoading || !data ? (
        <div className="grid gap-3 md:grid-cols-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <StatCard
              label="Total requisitions"
              value={data.stats.total_requisitions.toString()}
            />
            <StatCard
              label="Published"
              value={data.stats.published.toString()}
              hint={`${data.stats.draft} draft · ${data.stats.failed} failed`}
            />
            <StatCard
              label="KB-grounded"
              value={`${Math.round(data.stats.grounded_rate * 100)}%`}
              hint="JDs anchored in your knowledge base"
              progress={data.stats.grounded_rate}
            />
            <StatCard
              label="Median time-to-publish"
              value={formatDuration(data.stats.median_seconds_to_publish)}
              hint="From generate to ATS post"
            />
          </div>

          <section className="grid gap-4 md:grid-cols-2">
            <div className="rounded-lg border border-border bg-background p-4">
              <h2 className="mb-3 text-sm font-medium">By ATS</h2>
              {data.by_ats.length === 0 ? (
                <p className="text-xs text-muted-foreground">No publishes yet.</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {data.by_ats.map((row) => (
                    <li key={row.platform} className="flex items-center justify-between">
                      <span className="capitalize">{row.platform}</span>
                      <Badge variant="outline">{row.count}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="rounded-lg border border-border bg-background p-4">
              <h2 className="mb-3 text-sm font-medium">Top recruiters</h2>
              {data.top_recruiters.length === 0 ? (
                <p className="text-xs text-muted-foreground">No activity yet.</p>
              ) : (
                <ul className="space-y-1.5 text-sm">
                  {data.top_recruiters.slice(0, 6).map((r) => (
                    <li
                      key={r.user_id}
                      className="flex items-center justify-between gap-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium">{r.name}</p>
                        {r.email && (
                          <p className="truncate text-xs text-muted-foreground">
                            {r.email}
                          </p>
                        )}
                      </div>
                      <Badge variant="outline">{r.requisitions}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="rounded-lg border border-border bg-background p-4">
            <h2 className="mb-3 text-sm font-medium">Recent audit log</h2>
            {data.recent_audit.length === 0 ? (
              <p className="text-xs text-muted-foreground">No actions yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <th className="pb-2 pr-3">When</th>
                      <th className="pb-2 pr-3">Action</th>
                      <th className="pb-2 pr-3">ATS</th>
                      <th className="pb-2 pr-3">Status</th>
                      <th className="pb-2 pr-3">Duration</th>
                      <th className="pb-2">Detail</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {data.recent_audit.map((row) => (
                      <tr key={row.id}>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {new Date(row.created_at).toLocaleString()}
                        </td>
                        <td className="py-2 pr-3 font-medium">{row.action}</td>
                        <td className="py-2 pr-3 capitalize">
                          {row.ats_platform ?? "—"}
                        </td>
                        <td className="py-2 pr-3">
                          {row.status === "success" ? (
                            <span className="inline-flex items-center gap-1 text-emerald-600">
                              <CheckCircle2 className="h-3 w-3" /> ok
                            </span>
                          ) : row.status === "failure" ? (
                            <span className="inline-flex items-center gap-1 text-red-600">
                              <AlertTriangle className="h-3 w-3" /> fail
                            </span>
                          ) : (
                            <span className="text-muted-foreground">skipped</span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {row.duration_ms ? `${row.duration_ms}ms` : "—"}
                        </td>
                        <td className="py-2 text-muted-foreground">
                          {row.error_message ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
