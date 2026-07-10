"use client";

import useSWR from "swr";
import { BarChart3, Clock, ShieldAlert, Send, Sparkles } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface SupportMetrics {
  window_days: number;
  total_tickets: number;
  by_status: Record<string, number>;
  drafts_total: number;
  escalations: number;
  escalation_rate: number;
  sent: number;
  auto_sent: number;
  rejected: number;
  resolved: number;
  resolved_rate: number;
  auto_send_rate: number;
  avg_confidence: number | null;
  avg_time_to_resolve_seconds: number | null;
}

const fetcher = async (url: string): Promise<SupportMetrics> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

function humanizeDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

interface StatProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  tone?: string;
}

function Stat({ icon, label, value, sub, tone }: StatProps) {
  return (
    <div className="min-w-[130px] flex-1 rounded-xl border border-border bg-card px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.04em] text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className={cn("mt-1 text-xl font-extrabold tabular-nums", tone)}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function MetricsStrip() {
  const { data, error, isLoading } = useSWR<SupportMetrics>(
    "/api/admin/support/metrics?window_days=30",
    fetcher,
    { refreshInterval: 30_000 },
  );

  if (error) return null; // metrics are non-critical; don't block the queue
  if (isLoading && !data) {
    return (
      <div className="mb-6 flex flex-wrap gap-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-[70px] min-w-[130px] flex-1 rounded-xl" />
        ))}
      </div>
    );
  }
  if (!data || data.total_tickets === 0) return null;

  const escalationTone =
    data.escalation_rate >= 0.5
      ? "text-destructive-ink"
      : data.escalation_rate >= 0.25
        ? "text-amber-ink"
        : "text-foreground";

  return (
    <div className="mb-6">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        <BarChart3 className="h-3.5 w-3.5" />
        Last {data.window_days} days
      </div>
      <div className="flex flex-wrap gap-2">
        <Stat
          icon={<BarChart3 className="h-3 w-3" />}
          label="Tickets"
          value={String(data.total_tickets)}
          sub={`${data.resolved} resolved (${pct(data.resolved_rate)})`}
        />
        <Stat
          icon={<ShieldAlert className="h-3 w-3" />}
          label="Escalation rate"
          value={pct(data.escalation_rate)}
          sub={`${data.escalations}/${data.drafts_total} drafts`}
          tone={escalationTone}
        />
        <Stat
          icon={<Send className="h-3 w-3" />}
          label="Auto-sent"
          value={pct(data.auto_send_rate)}
          sub={`${data.auto_sent} of ${data.sent + data.auto_sent} sent`}
        />
        <Stat
          icon={<Sparkles className="h-3 w-3" />}
          label="Avg confidence"
          value={data.avg_confidence !== null ? `${data.avg_confidence.toFixed(1)}/10` : "—"}
          sub="non-escalation drafts"
        />
        <Stat
          icon={<Clock className="h-3 w-3" />}
          label="Time to resolve"
          value={humanizeDuration(data.avg_time_to_resolve_seconds)}
          sub="avg, resolved tickets"
        />
      </div>
    </div>
  );
}
