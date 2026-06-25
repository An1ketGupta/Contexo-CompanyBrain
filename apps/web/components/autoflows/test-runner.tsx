"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, Loader2, Play, XCircle } from "lucide-react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { getTrigger } from "@/lib/autoflow/triggers";
import { getAction } from "@/lib/autoflow/catalog";
import type { AutoflowDraft, AutoflowRunStep } from "@/lib/autoflow/types";

interface TestRunnerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  autoflowId: string;
  draft: AutoflowDraft;
}

interface TestRunResult {
  run_id?: string;
  status?: string;
  steps?: AutoflowRunStep[];
  error_message?: string | null;
  blocking_approval_id?: string | null;
  [k: string]: unknown;
}

export function TestRunner({ open, onOpenChange, autoflowId, draft }: TestRunnerProps) {
  const sample = useMemo(() => buildSamplePayload(draft.trigger_type), [draft.trigger_type]);
  const [payload, setPayload] = useState<string>(JSON.stringify(sample, null, 2));
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<TestRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleRun = async () => {
    setRunning(true);
    setResult(null);
    setError(null);
    try {
      let parsed: Record<string, unknown> = {};
      if (payload.trim()) {
        try {
          parsed = JSON.parse(payload);
        } catch {
          throw new Error("Mock payload must be valid JSON.");
        }
      }
      const res = await fetch(`/api/admin/autoflows/${autoflowId}/run-now`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger_payload: parsed }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          (body as { detail?: string })?.detail || `Run failed (${res.status})`,
        );
      }
      setResult(body as TestRunResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test run failed.");
    } finally {
      setRunning(false);
    }
  };

  const resetPayload = () => setPayload(JSON.stringify(sample, null, 2));

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full max-w-xl flex-col overflow-hidden sm:max-w-xl">
        <SheetTitle className="text-base">Test this autoflow</SheetTitle>
        <p className="-mt-3 text-xs text-muted-foreground">
          Runs the saved flow with a mock <code>trigger_payload</code>. Use this to verify a flow
          before turning it loose on real events.
        </p>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-medium">Mock trigger payload</Label>
            <Button variant="ghost" size="sm" onClick={resetPayload} className="h-7 text-xs">
              Reset sample
            </Button>
          </div>
          <Textarea
            rows={10}
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            className="font-mono text-xs"
          />
        </div>

        <div className="flex items-center gap-2">
          <Button onClick={handleRun} disabled={running} className="gap-1">
            {running ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Run test
          </Button>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>

        {result && (
          <div className="flex-1 space-y-3 overflow-y-auto rounded border bg-muted/20 p-3">
            <div className="flex items-center gap-2">
              <StatusBadge status={result.status ?? "unknown"} />
              {result.run_id && (
                <span className="font-mono text-[10px] text-muted-foreground">
                  run {result.run_id.slice(0, 8)}
                </span>
              )}
            </div>
            {result.error_message && (
              <div className="rounded border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                {result.error_message}
              </div>
            )}
            <StepTimeline steps={result.steps ?? []} actions={draft.actions} />
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { variant: "default" | "warning" | "outline" | "destructive" | "success"; icon?: React.ReactNode }> = {
    completed: { variant: "success", icon: <CheckCircle2 className="size-3" /> },
    failed: { variant: "destructive", icon: <XCircle className="size-3" /> },
    held_for_approval: { variant: "warning" },
    running: { variant: "warning" },
    pending: { variant: "outline" },
    cancelled: { variant: "outline" },
  };
  const cfg = map[status] ?? { variant: "outline" as const };
  return (
    <Badge variant={cfg.variant} className="gap-1 capitalize">
      {cfg.icon}
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

function StepTimeline({
  steps,
  actions,
}: {
  steps: AutoflowRunStep[];
  actions: AutoflowDraft["actions"];
}) {
  if (steps.length === 0) {
    return <p className="text-xs text-muted-foreground">No step output recorded.</p>;
  }
  return (
    <ol className="space-y-2">
      {steps.map((step) => {
        const action = actions.find((a) => a.order === step.index);
        const entry = action ? getAction(action.type) : null;
        return (
          <li key={step.index} className="rounded border bg-background p-2 text-xs">
            <div className="flex items-center gap-2">
              <StatusBadge status={step.status} />
              <span className="font-mono text-muted-foreground">
                {step.index + 1}. {entry?.shortLabel ?? step.type}
              </span>
            </div>
            {step.error && <p className="mt-1 text-destructive">{step.error}</p>}
            {step.output && Object.keys(step.output).length > 0 && (
              <pre className="mt-1 overflow-x-auto rounded bg-muted/50 p-2 text-[10px]">
                {JSON.stringify(step.output, null, 2)}
              </pre>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function buildSamplePayload(triggerType: AutoflowDraft["trigger_type"]): Record<string, unknown> {
  const entry = getTrigger(triggerType);
  const out: Record<string, unknown> = { test: true };
  for (const f of entry.payloadFields) {
    out[f.path] = sampleFor(f.path, f.example);
  }
  return out;
}

function sampleFor(path: string, example?: string): unknown {
  if (example) return example;
  if (path.includes("id")) return "00000000-0000-0000-0000-000000000000";
  if (path.includes("email")) return "teammate@example.com";
  if (path.includes("name") || path.includes("topic")) return "Sample value";
  if (path.includes("status")) return "completed";
  if (path.endsWith("_at")) return new Date().toISOString();
  if (path === "tags") return ["pricing"];
  if (path.includes("count") || path.includes("frequency")) return 1;
  return "";
}
