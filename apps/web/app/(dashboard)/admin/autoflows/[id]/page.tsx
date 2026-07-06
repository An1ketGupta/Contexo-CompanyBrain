"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  Edit,
  Play,
  XCircle,
} from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TestRunner } from "@/components/autoflows/test-runner";
import { getTrigger } from "@/lib/autoflow/triggers";
import { getAction } from "@/lib/autoflow/catalog";
import { getIcon } from "@/lib/autoflow/icons";
import type { AutoflowRow, AutoflowRun, AutoflowDraft } from "@/lib/autoflow/types";

const STATUS_VARIANT: Record<
  AutoflowRun["status"],
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
  if (res.status === 404) throw new Error("Autoflow not found.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function AutoflowDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const { id } = use(params);
  const [testOpen, setTestOpen] = useState(false);

  const {
    data: autoflow,
    error,
    isLoading,
    mutate: mutateFlow,
  } = useSWR<AutoflowRow>(`/api/admin/autoflows/${id}`, fetcher);

  const { data: runsData, mutate: mutateRuns } = useSWR<{ runs: AutoflowRun[] }>(
    `/api/admin/autoflows/${id}/runs`,
    fetcher,
    { refreshInterval: 5_000 },
  );

  const toggleActive = async () => {
    if (!autoflow) return;
    const next = !autoflow.is_active;
    await mutateFlow({ ...autoflow, is_active: next }, { revalidate: false });
    try {
      const res = await fetch(`/api/admin/autoflows/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: next }),
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      toast.success(next ? "Activated" : "Paused");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
      mutateFlow();
    }
  };

  const remove = async () => {
    if (!autoflow) return;
    if (!confirm(`Delete "${autoflow.name}"? This can't be undone.`)) return;
    const res = await fetch(`/api/admin/autoflows/${id}`, { method: "DELETE" });
    if (res.ok || res.status === 204) {
      toast.success("Deleted");
      router.push("/admin/autoflows");
    } else {
      toast.error(`Delete failed (${res.status})`);
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
        <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive-soft p-4 text-sm text-destructive-ink">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading || !autoflow ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <>
          <header className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0 flex-1 space-y-2">
              <h1 className="text-3xl font-extrabold tracking-tight">{autoflow.name}</h1>
              {autoflow.description && (
                <p className="max-w-[64ch] text-[15px] leading-relaxed text-muted-foreground">{autoflow.description}</p>
              )}
              <FlowSummary autoflow={autoflow} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" onClick={toggleActive}>
                {autoflow.is_active ? "Pause" : "Activate"}
              </Button>
              <Button variant="outline" onClick={() => setTestOpen(true)} className="gap-1">
                <Play className="size-4" />
                Test
              </Button>
              <Button asChild className="gap-1">
                <Link href={`/admin/autoflows/${id}/edit`}>
                  <Edit className="size-4" />
                  Edit
                </Link>
              </Button>
            </div>
          </header>

          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="runs">
                Run history ({runsData?.runs?.length ?? 0})
              </TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="mt-4">
              <FlowOverview autoflow={autoflow} />
            </TabsContent>

            <TabsContent value="runs" className="mt-4">
              <RunHistory runs={runsData?.runs ?? []} actions={autoflow.actions} onRefresh={mutateRuns} />
            </TabsContent>

            <TabsContent value="settings" className="mt-4">
              <div className="space-y-4 rounded-2xl border border-border bg-card p-6">
                <div>
                  <p className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                    Danger zone
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Deletes the autoflow and its run history. Cannot be undone.
                  </p>
                </div>
                <Button variant="destructive" onClick={remove}>
                  Delete autoflow
                </Button>
              </div>
            </TabsContent>
          </Tabs>

          <TestRunner
            open={testOpen}
            onOpenChange={setTestOpen}
            autoflowId={id}
            draft={autoflow as unknown as AutoflowDraft}
          />
        </>
      )}
    </div>
  );
}

function FlowSummary({ autoflow }: { autoflow: AutoflowRow }) {
  const trigger = getTrigger(autoflow.trigger_type);
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Badge variant="outline">{trigger.label}</Badge>
      {autoflow.trigger_type === "scheduled" && autoflow.trigger_config?.cron && (
        <Badge variant="accent" className="font-mono">{autoflow.trigger_config.cron}</Badge>
      )}
      {!autoflow.is_active && <Badge variant="outline">Paused</Badge>}
      {autoflow.confidence_threshold != null && (
        <Badge variant="brand">Gate ≥ {(autoflow.confidence_threshold * 100).toFixed(0)}%</Badge>
      )}
      {autoflow.last_fired_at && (
        <span className="text-muted-foreground">
          last fired {new Date(autoflow.last_fired_at).toLocaleString()}
        </span>
      )}
    </div>
  );
}

function FlowOverview({ autoflow }: { autoflow: AutoflowRow }) {
  const trigger = getTrigger(autoflow.trigger_type);
  const TriggerIcon = getIcon(trigger.icon);
  const sorted = autoflow.actions.slice().sort((a, b) => a.order - b.order);

  return (
    <div className="space-y-2 rounded-2xl border border-border bg-card p-6">
      <div className="flex items-start gap-3 border-b border-border pb-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-tint text-amber-ink">
          <TriggerIcon className="size-5" />
        </div>
        <div>
          <span className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-amber-ink">
            When
          </span>
          <p className="mt-1 text-sm font-semibold">{trigger.label}</p>
          {autoflow.trigger_config?.filters &&
            Object.keys(autoflow.trigger_config.filters).length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                Filters:{" "}
                <code className="font-mono">
                  {JSON.stringify(autoflow.trigger_config.filters)}
                </code>
              </p>
            )}
        </div>
      </div>

      <ol className="space-y-2 pt-3">
        {sorted.map((a, i) => {
          const entry = getAction(a.type);
          const Icon = getIcon(entry.icon);
          return (
            <li key={i} className="flex items-start gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-muted text-muted-foreground">
                <Icon className="size-4" />
              </div>
              <div className="min-w-0 flex-1">
                <span className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">Step {i + 1}</span>
                <p className="mt-1 text-sm font-semibold">{entry.label}</p>
                <ConfigPreview config={a.config} />
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function ConfigPreview({ config }: { config: Record<string, unknown> }) {
  const entries = Object.entries(config).filter(([_, v]) => v !== "" && v != null);
  if (entries.length === 0) return null;
  return (
    <dl className="mt-2 space-y-1 text-xs">
      {entries.map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="shrink-0 font-mono text-muted-foreground">{k}:</dt>
          <dd className="min-w-0 break-words">
            {typeof v === "string" ? v : JSON.stringify(v)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function RunHistory({
  runs,
  actions,
  onRefresh,
}: {
  runs: AutoflowRun[];
  actions: AutoflowRow["actions"];
  onRefresh: () => void;
}) {
  if (!runs.length) {
    return (
      <div className="rounded-2xl border border-dashed border-border bg-muted/40 px-6 py-12 text-center text-sm text-muted-foreground">
        No runs yet. Hit <strong>Test</strong> to fire one with a mock payload.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          Refresh
        </Button>
      </div>
      <ul className="space-y-2">
        {runs.map((run) => (
          <li key={run.id} className="rounded-2xl border border-border bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={STATUS_VARIANT[run.status]} className="capitalize">
                  {run.status.replace(/_/g, " ")}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {run.steps_completed}/{run.total_steps} steps
                </span>
              </div>
              <span className="text-xs text-muted-foreground">
                {new Date(run.started_at).toLocaleString()}
              </span>
            </div>
            {run.error_message && (
              <p className="mt-2 text-xs text-destructive-ink">{run.error_message}</p>
            )}
            {run.steps.length > 0 && (
              <ol className="mt-2 space-y-1">
                {run.steps.map((step) => {
                  const action = actions.find((a) => a.order === step.index);
                  const entry = action ? getAction(action.type) : null;
                  return (
                    <li key={step.index} className="flex items-center gap-2 text-xs">
                      {step.status === "completed" ? (
                        <CheckCircle2 className="size-3.5 text-success-ink" />
                      ) : step.status === "failed" ? (
                        <XCircle className="size-3.5 text-destructive-ink" />
                      ) : (
                        <span className="size-3.5 rounded-full border border-muted-foreground/40" />
                      )}
                      <span className="font-mono text-muted-foreground">
                        {step.index + 1}. {entry?.shortLabel ?? step.type}
                      </span>
                      <span className="text-muted-foreground">
                        {step.status.replace(/_/g, " ")}
                      </span>
                      {step.error && <span className="text-destructive-ink">· {step.error}</span>}
                    </li>
                  );
                })}
              </ol>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
