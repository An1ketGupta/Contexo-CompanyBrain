"use client";

import { use, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, ChevronLeft, Play } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface AutoflowRow {
  id: string;
  name: string;
  description: string | null;
  trigger_type: string;
  trigger_config: { cron?: string; filters?: Record<string, unknown> };
  actions: Array<{ type: string; order: number; config: Record<string, unknown> }>;
  confidence_threshold: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_fired_at: string | null;
}

interface RunStep {
  index: number;
  type: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  error?: string;
  output?: Record<string, unknown>;
}

interface AutoflowRun {
  id: string;
  autoflow_id: string;
  status: "pending" | "running" | "completed" | "failed" | "held_for_approval" | "cancelled";
  steps: RunStep[];
  steps_completed: number;
  total_steps: number;
  error_message: string | null;
  blocking_approval_id: string | null;
  started_at: string;
  completed_at: string | null;
  trigger_payload: Record<string, unknown>;
}

const STATUS_VARIANT: Record<
  AutoflowRun["status"],
  "default" | "secondary" | "outline" | "destructive"
> = {
  pending: "outline",
  running: "secondary",
  completed: "default",
  failed: "destructive",
  held_for_approval: "secondary",
  cancelled: "outline",
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (res.status === 404) throw new Error("Autoflow not found.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function AutoflowDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [running, setRunning] = useState(false);
  const [lastRunResult, setLastRunResult] = useState<string | null>(null);

  const {
    data: autoflow,
    error,
    isLoading,
  } = useSWR<AutoflowRow>(`/api/admin/autoflows/${id}`, fetcher);
  const {
    data: runsData,
    mutate: mutateRuns,
  } = useSWR<{ runs: AutoflowRun[] }>(`/api/admin/autoflows/${id}/runs`, fetcher, {
    refreshInterval: 5_000,
  });

  const handleRunNow = async () => {
    setRunning(true);
    setLastRunResult(null);
    try {
      const res = await fetch(`/api/admin/autoflows/${id}/run-now`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger_payload: { test: true } }),
      });
      const body = await res.json();
      setLastRunResult(JSON.stringify(body));
      mutateRuns();
    } catch (e) {
      setLastRunResult(`Error: ${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <Link
        href="/admin/autoflows"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="size-4" /> Back to autoflows
      </Link>

      {error ? (
        <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading || !autoflow ? (
        <Skeleton className="h-32 w-full" />
      ) : (
        <>
          <header className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{autoflow.name}</h1>
              {autoflow.description && (
                <p className="mt-1 text-sm text-muted-foreground">{autoflow.description}</p>
              )}
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <Badge variant="outline" className="font-mono">
                  {autoflow.trigger_type}
                </Badge>
                {autoflow.trigger_type === "scheduled" && autoflow.trigger_config.cron && (
                  <Badge variant="secondary" className="font-mono">
                    {autoflow.trigger_config.cron}
                  </Badge>
                )}
                {!autoflow.is_active && <Badge variant="outline">Inactive</Badge>}
                {autoflow.confidence_threshold != null && (
                  <Badge variant="secondary">
                    Gate ≥ {(autoflow.confidence_threshold * 100).toFixed(0)}%
                  </Badge>
                )}
              </div>
            </div>
            <Button onClick={handleRunNow} disabled={running} className="gap-2">
              <Play className="size-4" />
              {running ? "Running…" : "Run now"}
            </Button>
          </header>

          <section className="rounded-md border bg-card p-4">
            <h2 className="text-sm font-medium">Actions</h2>
            <ol className="mt-3 space-y-2">
              {[...autoflow.actions]
                .sort((a, b) => a.order - b.order)
                .map((a, i) => (
                  <li key={i} className="flex items-start gap-3 text-sm">
                    <span className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {i + 1}.
                    </span>
                    <Badge variant="outline" className="font-mono">
                      {a.type}
                    </Badge>
                    <pre className="flex-1 overflow-x-auto rounded bg-muted px-2 py-1 text-xs">
                      {JSON.stringify(a.config, null, 2)}
                    </pre>
                  </li>
                ))}
            </ol>
          </section>

          {lastRunResult && (
            <pre className="overflow-x-auto rounded-md border bg-card p-3 text-xs">
              {lastRunResult}
            </pre>
          )}

          <section className="rounded-md border bg-card p-4">
            <h2 className="text-sm font-medium">Run history</h2>
            {!runsData?.runs.length ? (
              <p className="mt-3 text-xs text-muted-foreground">No runs yet.</p>
            ) : (
              <ul className="mt-3 space-y-2">
                {runsData.runs.map((run) => (
                  <li key={run.id} className="rounded border p-3 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Badge variant={STATUS_VARIANT[run.status]}>{run.status}</Badge>
                        <span className="text-muted-foreground">
                          {run.steps_completed}/{run.total_steps} steps
                        </span>
                      </div>
                      <span className="text-muted-foreground">
                        {new Date(run.started_at).toLocaleString()}
                      </span>
                    </div>
                    {run.error_message && (
                      <p className="mt-2 text-destructive">{run.error_message}</p>
                    )}
                    {run.steps.length > 0 && (
                      <ol className="mt-2 space-y-1">
                        {run.steps.map((step) => (
                          <li key={step.index} className="flex items-center gap-2">
                            <Badge variant="outline" className="font-mono text-[10px]">
                              {step.status}
                            </Badge>
                            <span className="font-mono">{step.type}</span>
                            {step.error && (
                              <span className="text-destructive">{step.error}</span>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
