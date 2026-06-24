"use client";

import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, Plus, Zap } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

// Thin admin index: list autoflows + their last-run snapshot. The full
// drag-and-drop builder lands in Day 6; this page exists so Days 1-5 are
// observably alive and admins can flip is_active without writing SQL.

interface AutoflowRow {
  id: string;
  name: string;
  description: string | null;
  trigger_type: string;
  trigger_config: { cron?: string; filters?: Record<string, unknown> };
  actions: Array<{ type: string; order: number }>;
  confidence_threshold: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_fired_at: string | null;
}

interface ListResponse {
  autoflows: AutoflowRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const TRIGGER_LABELS: Record<string, string> = {
  document_uploaded: "Doc uploaded",
  document_ready: "Doc ready",
  document_failed: "Doc failed",
  query_no_results: "Query: no results",
  message_feedback_negative: "Negative feedback",
  scheduled: "Scheduled",
  employee_joined: "Employee joined",
  knowledge_gap_detected: "Knowledge gap",
  approval_requested: "Approval requested",
  agent_completed: "Agent completed",
  compliance_acknowledged: "Compliance ack",
};

export default function AutoflowsPage() {
  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/admin/autoflows",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Autoflows</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Trigger-action automations. When the trigger fires, the actions run in order.
            Full visual builder ships in Day 6 — for now create/edit autoflows via API.
          </p>
        </div>
        <Button disabled className="gap-2" title="Visual builder ships in Day 6">
          <Plus className="size-4" /> New autoflow
        </Button>
      </header>

      {error ? (
        <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : !data?.autoflows.length ? (
        <div className="rounded-md border border-dashed p-10 text-center">
          <Zap className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No autoflows yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Autoflows fire on events like document uploads, knowledge gaps, or scheduled crons.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {data.autoflows.map((flow) => (
            <li
              key={flow.id}
              className="rounded-md border bg-card p-4 transition-colors hover:bg-accent/30"
            >
              <Link
                href={`/admin/autoflows/${flow.id}`}
                className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="truncate text-base font-medium">{flow.name}</h2>
                    {!flow.is_active && <Badge variant="outline">Inactive</Badge>}
                    {flow.confidence_threshold != null && (
                      <Badge variant="secondary">
                        Gate at {(flow.confidence_threshold * 100).toFixed(0)}%
                      </Badge>
                    )}
                  </div>
                  {flow.description && (
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {flow.description}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span>WHEN</span>
                    <Badge variant="outline" className="font-mono">
                      {TRIGGER_LABELS[flow.trigger_type] ?? flow.trigger_type}
                      {flow.trigger_type === "scheduled" && flow.trigger_config.cron
                        ? ` (${flow.trigger_config.cron})`
                        : ""}
                    </Badge>
                    <span>THEN</span>
                    {[...flow.actions]
                      .sort((a, b) => a.order - b.order)
                      .map((a, i) => (
                        <Badge key={i} variant="outline" className="font-mono">
                          {a.type}
                        </Badge>
                      ))}
                  </div>
                </div>
                <div className="shrink-0 text-right text-xs text-muted-foreground">
                  {flow.last_fired_at ? (
                    <span>last fired {new Date(flow.last_fired_at).toLocaleString()}</span>
                  ) : (
                    <span className="italic">never fired</span>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
