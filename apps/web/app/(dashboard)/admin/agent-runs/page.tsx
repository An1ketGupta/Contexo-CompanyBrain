"use client";

import { useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Loader2,
  XCircle,
} from "lucide-react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "@/lib/date";

interface AgentRunRow {
  id: string;
  agent_type: string;
  triggered_by: string;
  triggered_by_user_id: string | null;
  triggered_by_name?: string | null;
  status: string;
  llm_tokens_used: number;
  confidence_scores: number[];
  avg_confidence?: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  approval_id: string | null;
}

interface AgentRunDetail extends AgentRunRow {
  steps: Array<{
    step_name: string;
    status: string;
    result: Record<string, unknown> | null;
    error: string | null;
    timestamp: string;
  }>;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  approval?: { id: string; status: string; resolved_at: string | null } | null;
}

interface Summary {
  period: string;
  total_runs: number;
  by_agent_type: Record<string, number>;
  by_status: Record<string, number>;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_META: Record<string, { label: string; cls: string; icon: React.ComponentType<{ className?: string }> }> = {
  running: { label: "Running", cls: "bg-blue-500/10 text-blue-700 dark:text-blue-300", icon: Loader2 },
  completed: { label: "Completed", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", icon: CheckCircle2 },
  failed: { label: "Failed", cls: "bg-red-500/10 text-red-700 dark:text-red-300", icon: XCircle },
  cancelled: { label: "Cancelled", cls: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400", icon: XCircle },
  pending_approval: { label: "Awaiting approval", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-300", icon: Clock },
};

export default function AgentRunsPage() {
  const [agentType, setAgentType] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [period] = useState<"7d" | "30d" | "90d">("30d");

  const listUrl = useMemo(() => {
    const sp = new URLSearchParams();
    if (agentType) sp.set("agent_type", agentType);
    if (status) sp.set("status", status);
    sp.set("limit", "100");
    return `/api/admin/agent-runs?${sp.toString()}`;
  }, [agentType, status]);

  const { data, error, isLoading } = useSWR<{ runs: AgentRunRow[]; total: number }>(
    listUrl,
    fetcher,
  );
  const summary = useSWR<Summary>(
    `/api/admin/agent-runs/stats/summary?period=${period}`,
    fetcher,
  );

  const [activeId, setActiveId] = useState<string | null>(null);
  const detail = useSWR<AgentRunDetail>(
    activeId ? `/api/admin/agent-runs/${activeId}` : null,
    fetcher,
  );

  const agentTypes = useMemo(
    () => Object.keys(summary.data?.by_agent_type ?? {}).sort(),
    [summary.data],
  );
  const statuses = useMemo(
    () => Object.keys(summary.data?.by_status ?? {}).sort(),
    [summary.data],
  );

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Agent runs</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Audit trail for every autonomous agent execution.
        </p>
      </header>

      {summary.data ? (
        <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-4">
          <StatCard label="Total runs" value={summary.data.total_runs} icon={Activity} />
          <StatCard
            label="Completed"
            value={summary.data.by_status.completed ?? 0}
            icon={CheckCircle2}
          />
          <StatCard
            label="Failed"
            value={summary.data.by_status.failed ?? 0}
            icon={AlertCircle}
            tone="warn"
          />
          <StatCard
            label="Awaiting approval"
            value={summary.data.by_status.pending_approval ?? 0}
            icon={Clock}
          />
        </div>
      ) : null}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <FilterChip
          label="All agents"
          active={agentType === null}
          onClick={() => setAgentType(null)}
        />
        {agentTypes.map((t) => (
          <FilterChip
            key={t}
            label={prettyAgent(t)}
            active={agentType === t}
            onClick={() => setAgentType(t)}
          />
        ))}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <FilterChip
            label="All statuses"
            active={status === null}
            onClick={() => setStatus(null)}
          />
          {statuses.map((s) => (
            <FilterChip
              key={s}
              label={STATUS_META[s]?.label ?? s}
              active={status === s}
              onClick={() => setStatus(s)}
            />
          ))}
        </div>
      </div>

      {isLoading ? (
        <ul className="divide-y rounded-md border bg-card">
          {Array.from({ length: 5 }).map((_, i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-3">
              <Skeleton className="h-9 w-9 shrink-0 rounded-md" />
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-center gap-2">
                  <Skeleton
                    className="h-3.5"
                    style={{ width: `${35 + ((i * 11) % 30)}%` }}
                  />
                  <Skeleton className="h-4 w-14 rounded-full" />
                </div>
                <Skeleton className="h-2.5 w-1/3" />
              </div>
              <Skeleton className="h-4 w-4 rounded" />
            </li>
          ))}
        </ul>
      ) : error ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          {error instanceof Error ? error.message : "Couldn't load runs."}
        </div>
      ) : (data?.runs ?? []).length === 0 ? (
        <div className="rounded-md border bg-card px-6 py-12 text-center">
          <Activity className="mx-auto h-8 w-8 text-muted-foreground/50" />
          <p className="mt-3 text-sm text-muted-foreground">
            No agent runs yet. They appear here as soon as an agent fires.
          </p>
        </div>
      ) : (
        <ul className="divide-y rounded-md border bg-card">
          {(data?.runs ?? []).map((run) => (
            <RunRow key={run.id} run={run} onClick={() => setActiveId(run.id)} />
          ))}
        </ul>
      )}

      <Dialog open={Boolean(activeId)} onOpenChange={(o) => !o && setActiveId(null)}>
        {activeId ? (
          <DialogContent className="max-w-3xl">
            <DialogHeader>
              <DialogTitle>
                {detail.data ? prettyAgent(detail.data.agent_type) : "Agent run"}
              </DialogTitle>
              <DialogDescription>
                {detail.data ? (
                  <>
                    Triggered by {detail.data.triggered_by}
                    {detail.data.triggered_by_name
                      ? ` (${detail.data.triggered_by_name})`
                      : ""}{" "}
                    · {formatDistanceToNow(detail.data.created_at)}
                  </>
                ) : (
                  "Loading…"
                )}
              </DialogDescription>
            </DialogHeader>

            {detail.isLoading ? (
              <div className="space-y-3 py-4">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-32 w-full rounded-md" />
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-24 w-full rounded-md" />
              </div>
            ) : detail.error ? (
              <div className="text-sm text-destructive">
                {detail.error instanceof Error ? detail.error.message : "Failed"}
              </div>
            ) : detail.data ? (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-3 text-xs">
                  <Stat label="Status" value={STATUS_META[detail.data.status]?.label ?? detail.data.status} />
                  <Stat label="Tokens" value={detail.data.llm_tokens_used.toLocaleString()} />
                  {detail.data.confidence_scores?.length ? (
                    <Stat
                      label="Avg confidence"
                      value={`${(
                        detail.data.confidence_scores.reduce((s, n) => s + n, 0) /
                        detail.data.confidence_scores.length
                      ).toFixed(1)} / 10`}
                    />
                  ) : null}
                  {detail.data.started_at && detail.data.completed_at ? (
                    <Stat
                      label="Duration"
                      value={formatDuration(detail.data.started_at, detail.data.completed_at)}
                    />
                  ) : null}
                </div>

                {detail.data.error ? (
                  <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                    {detail.data.error}
                  </div>
                ) : null}

                <div>
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Steps
                  </div>
                  <ol className="space-y-2">
                    {(detail.data.steps ?? []).map((s, i) => (
                      <li
                        key={i}
                        className="rounded-md border bg-card p-3 text-[13px]"
                      >
                        <div className="flex items-center justify-between">
                          <div className="font-medium">{s.step_name}</div>
                          <span
                            className={cn(
                              "rounded-md px-2 py-0.5 text-[10px] font-medium",
                              s.status === "completed" &&
                                "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                              s.status === "failed" &&
                                "bg-red-500/10 text-red-700 dark:text-red-300",
                              s.status === "started" &&
                                "bg-blue-500/10 text-blue-700 dark:text-blue-300",
                              s.status === "skipped" &&
                                "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
                            )}
                          >
                            {s.status}
                          </span>
                        </div>
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {new Date(s.timestamp).toLocaleString()}
                        </div>
                        {s.error ? (
                          <p className="mt-2 text-[12px] text-destructive">
                            {s.error}
                          </p>
                        ) : null}
                        {s.result ? (
                          <pre className="mt-2 max-h-40 overflow-y-auto rounded bg-muted/60 p-2 text-[11px] leading-relaxed">
                            {JSON.stringify(s.result, null, 2)}
                          </pre>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </div>

                {detail.data.output && Object.keys(detail.data.output).length ? (
                  <Collapsible label="Output">
                    <pre className="max-h-60 overflow-y-auto rounded bg-muted/60 p-2 text-[11px]">
                      {JSON.stringify(detail.data.output, null, 2)}
                    </pre>
                  </Collapsible>
                ) : null}

                {detail.data.input && Object.keys(detail.data.input).length ? (
                  <Collapsible label="Input">
                    <pre className="max-h-60 overflow-y-auto rounded bg-muted/60 p-2 text-[11px]">
                      {JSON.stringify(detail.data.input, null, 2)}
                    </pre>
                  </Collapsible>
                ) : null}
              </div>
            ) : null}
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}

function RunRow({ run, onClick }: { run: AgentRunRow; onClick: () => void }) {
  const meta = STATUS_META[run.status] ?? STATUS_META.completed;
  const Icon = meta.icon;
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40"
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
          <Icon className={cn("h-4 w-4", run.status === "running" && "animate-spin")} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">
              {prettyAgent(run.agent_type)}
            </span>
            <Badge variant="secondary" className={cn("h-5 px-2 text-[10px]", meta.cls)}>
              {meta.label}
            </Badge>
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {run.triggered_by_name ?? run.triggered_by}
            <span className="px-1.5">·</span>
            {formatDistanceToNow(run.created_at)}
            {run.llm_tokens_used ? (
              <>
                <span className="px-1.5">·</span>
                {run.llm_tokens_used.toLocaleString()} tokens
              </>
            ) : null}
            {run.avg_confidence != null ? (
              <>
                <span className="px-1.5">·</span>
                conf {run.avg_confidence}/10
              </>
            ) : null}
          </div>
        </div>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
      </button>
    </li>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-muted text-foreground"
          : "border border-input bg-background text-muted-foreground hover:bg-muted/50",
      )}
    >
      {label}
    </button>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  tone?: "warn";
}) {
  return (
    <div className="rounded-md border bg-card p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className={cn("h-3.5 w-3.5", tone === "warn" && "text-amber-500")} />
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="text-sm font-medium">{value}</div>
    </div>
  );
}

function Collapsible({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-xs font-medium"
      >
        <span>{label}</span>
        <ChevronRight className={cn("h-4 w-4 transition-transform", open && "rotate-90")} />
      </button>
      {open ? <div className="border-t p-2">{children}</div> : null}
    </div>
  );
}

function prettyAgent(t: string): string {
  return t
    .split("_")
    .map((s) => s[0]?.toUpperCase() + s.slice(1))
    .join(" ");
}

function formatDuration(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (!isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
