"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, ChevronLeft, Filter } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface AutoflowRow {
  id: string;
  name: string;
}

interface RunStep {
  index: number;
  type: string;
  status: string;
  error?: string;
}

interface ActivityRun {
  id: string;
  autoflow_id: string;
  autoflow_name: string;
  status: "pending" | "running" | "completed" | "failed" | "held_for_approval" | "cancelled";
  steps: RunStep[];
  steps_completed: number;
  total_steps: number;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

interface ActivityResponse {
  runs: ActivityRun[];
  stats: {
    total: number;
    by_status: Record<string, number>;
    p95_duration_ms: number | null;
  };
}

const STATUS_VARIANT: Record<
  ActivityRun["status"],
  "default" | "warning" | "outline" | "destructive" | "success"
> = {
  pending: "outline",
  running: "warning",
  completed: "success",
  failed: "destructive",
  held_for_approval: "warning",
  cancelled: "outline",
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "held_for_approval", label: "Held for approval" },
  { value: "running", label: "Running" },
  { value: "cancelled", label: "Cancelled" },
];

export default function AutoflowsActivityPage() {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [flowFilter, setFlowFilter] = useState<string>("all");
  const [limit, setLimit] = useState<number>(100);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    p.set("limit", String(limit));
    if (statusFilter !== "all") p.set("status", statusFilter);
    if (flowFilter !== "all") p.set("autoflow_id", flowFilter);
    return p.toString();
  }, [statusFilter, flowFilter, limit]);

  const { data, error, isLoading, mutate } = useSWR<ActivityResponse>(
    `/api/admin/autoflows/_meta/activity?${params}`,
    fetcher,
    { refreshInterval: 10_000, revalidateOnFocus: false },
  );

  const { data: flowsData } = useSWR<{ autoflows: AutoflowRow[] }>(
    "/api/admin/autoflows",
    fetcher,
  );

  const stats = data?.stats;
  const successRate = stats?.total
    ? Math.round(((stats.by_status.completed ?? 0) / stats.total) * 100)
    : null;
  const failureRate = stats?.total
    ? Math.round(((stats.by_status.failed ?? 0) / stats.total) * 100)
    : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <Link
        href="/admin/autoflows"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="size-4" /> Back to autoflows
      </Link>

      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Live timeline of autoflow runs across every flow in your workspace.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => mutate()}>
          Refresh
        </Button>
      </header>

      <section className="grid gap-3 sm:grid-cols-4">
        <StatCard label="Runs" value={stats?.total ?? "—"} />
        <StatCard
          label="Success rate"
          value={successRate != null ? `${successRate}%` : "—"}
          tone={successRate != null && successRate >= 90 ? "good" : "neutral"}
        />
        <StatCard
          label="Failure rate"
          value={failureRate != null ? `${failureRate}%` : "—"}
          tone={failureRate != null && failureRate > 5 ? "bad" : "neutral"}
        />
        <StatCard
          label="p95 duration"
          value={
            stats?.p95_duration_ms != null
              ? formatDuration(stats.p95_duration_ms)
              : "—"
          }
        />
      </section>

      <section className="flex flex-wrap items-center gap-2 rounded-md border bg-card p-3">
        <Filter className="size-4 text-muted-foreground" />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={flowFilter} onValueChange={setFlowFilter}>
          <SelectTrigger className="w-64">
            <SelectValue placeholder="All flows" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All flows</SelectItem>
            {(flowsData?.autoflows ?? []).map((f) => (
              <SelectItem key={f.id} value={f.id}>
                {f.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v))}>
          <SelectTrigger className="w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="50">Last 50</SelectItem>
            <SelectItem value="100">Last 100</SelectItem>
            <SelectItem value="250">Last 250</SelectItem>
            <SelectItem value="500">Last 500</SelectItem>
          </SelectContent>
        </Select>
      </section>

      {error ? (
        <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : !data?.runs.length ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          No runs match these filters.
        </div>
      ) : (
        <ul className="space-y-2">
          {data.runs.map((run) => (
            <li key={run.id} className="rounded-md border bg-card p-3">
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant={STATUS_VARIANT[run.status]}>
                  {run.status.replace(/_/g, " ")}
                </Badge>
                <Link
                  href={`/admin/autoflows/${run.autoflow_id}`}
                  className="text-sm font-medium hover:underline"
                >
                  {run.autoflow_name}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {run.steps_completed}/{run.total_steps} steps
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatRelative(run.started_at)}
                </span>
                {run.completed_at && (
                  <span className="text-xs text-muted-foreground">
                    · {formatDuration(elapsedMs(run.started_at, run.completed_at))}
                  </span>
                )}
              </div>
              {run.error_message && (
                <p className="mt-2 text-xs text-destructive">{run.error_message}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: "good" | "bad" | "neutral";
}) {
  const toneClass =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "bad"
        ? "text-destructive"
        : "";
  return (
    <div className="rounded-md border bg-card p-3">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={`mt-1 text-xl font-semibold ${toneClass}`}>{value}</p>
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function elapsedMs(start: string, end: string): number {
  return Math.max(0, new Date(end).getTime() - new Date(start).getTime());
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleString();
}
