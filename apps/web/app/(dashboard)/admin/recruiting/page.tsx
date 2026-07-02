"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  FileText,
  Mail,
  MessageSquare,
  type LucideIcon,
  RefreshCw,
  Send,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/admin/stat-card";
import { cn } from "@/lib/utils";
import { formatAbsolute, formatRelativeShort } from "@/lib/date";

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

const ACTION_ICONS: Record<string, LucideIcon> = {
  candidate_sync: RefreshCw,
  notion_create: FileText,
  slack_notify: MessageSquare,
  hiring_manager_email: Mail,
  ats_publish: Send,
  publish_attempt: Send,
};

function formatAction(action: string): string {
  const label = action.replace(/_/g, " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
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
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">
            Recruiting analytics
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Hiring throughput, ATS distribution, and the audit trail for every
            publish action your org has run.
          </p>
        </div>
        <div className="flex shrink-0 gap-1 rounded-xl bg-muted p-1">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setPeriod(opt.value)}
              className={
                "rounded-lg px-3.5 py-1.5 text-xs font-bold transition-all " +
                (period === opt.value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {opt.label}
            </button>
          ))}
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : isLoading || !data ? (
        <div className="grid gap-4 md:grid-cols-4">
          <Skeleton className="h-28 rounded-2xl" />
          <Skeleton className="h-28 rounded-2xl" />
          <Skeleton className="h-28 rounded-2xl" />
          <Skeleton className="h-28 rounded-2xl" />
        </div>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-4">
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
            <div className="rounded-2xl border border-border bg-card p-5">
              <h2 className="mb-3 text-[15px] font-bold">By ATS</h2>
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

            <div className="rounded-2xl border border-border bg-card p-5">
              <h2 className="mb-3 text-[15px] font-bold">Top recruiters</h2>
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

          <section className="rounded-2xl border border-border bg-card p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-[15px] font-bold">Recent audit log</h2>
              {data.recent_audit.length > 0 ? (
                <Badge variant="outline">{data.recent_audit.length}</Badge>
              ) : null}
            </div>
            {data.recent_audit.length === 0 ? (
              <p className="text-xs text-muted-foreground">No actions yet.</p>
            ) : (
              <ul className="divide-y divide-border">
                {data.recent_audit.map((row) => {
                  const Icon = ACTION_ICONS[row.action] ?? Circle;
                  const tone =
                    row.status === "failure"
                      ? "destructive"
                      : row.status === "success"
                        ? "success"
                        : "muted";

                  return (
                    <li
                      key={row.id}
                      className="flex items-start gap-3 py-3 first:pt-0 last:pb-0"
                    >
                      <div
                        className={cn(
                          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                          tone === "success" && "bg-success-tint text-success-ink",
                          tone === "destructive" &&
                            "bg-destructive-soft text-destructive-ink",
                          tone === "muted" && "bg-muted text-muted-foreground",
                        )}
                      >
                        <Icon className="h-3.5 w-3.5" />
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="text-sm font-semibold">
                            {formatAction(row.action)}
                          </span>
                          {row.ats_platform ? (
                            <Badge variant="outline" className="capitalize">
                              {row.ats_platform}
                            </Badge>
                          ) : null}
                          {row.status === "success" ? (
                            <Badge variant="success">
                              <CheckCircle2 /> Success
                            </Badge>
                          ) : row.status === "failure" ? (
                            <Badge variant="destructive">
                              <AlertTriangle /> Failed
                            </Badge>
                          ) : (
                            <Badge variant="default">Skipped</Badge>
                          )}
                        </div>

                        {row.error_message ? (
                          <p
                            className="mt-1 truncate font-mono text-xs text-destructive-ink"
                            title={row.error_message}
                          >
                            {row.error_message}
                          </p>
                        ) : null}
                      </div>

                      <div
                        className="flex shrink-0 flex-col items-end gap-0.5 text-xs text-muted-foreground"
                        title={formatAbsolute(row.created_at)}
                      >
                        <span>{formatRelativeShort(row.created_at)}</span>
                        <span className="tabular-nums">
                          {row.duration_ms ? `${row.duration_ms}ms` : "—"}
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
