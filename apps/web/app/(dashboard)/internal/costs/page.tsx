"use client";

import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/admin/stat-card";
import { cn } from "@/lib/utils";

interface PerOrgRow {
  org_id: string;
  name: string;
  plan: string;
  cost_micros: number;
  cost_usd: number;
  cost_per_query_usd: number;
  tokens: number;
  queries: number;
  over_threshold: boolean;
}

interface ByModelRow {
  model: string;
  cost_usd: number;
  cost_micros: number;
  queries: number;
  tokens: number;
}

interface DailyPoint {
  date: string;
  cost_usd: number;
}

interface LlmCostsResponse {
  period: "7d" | "30d" | "90d";
  total_cost_usd: number;
  previous_period_cost_usd: number;
  delta_pct: number | null;
  total_tokens: number;
  total_queries: number;
  avg_cost_per_query_usd: number;
  per_org: PerOrgRow[];
  daily_cost: DailyPoint[];
  by_model: ByModelRow[];
}

const PERIODS: { label: string; value: "7d" | "30d" | "90d" }[] = [
  { label: "7 days", value: "7d" },
  { label: "30 days", value: "30d" },
  { label: "90 days", value: "90d" },
];

const fetcher = async (url: string): Promise<LlmCostsResponse> => {
  const res = await fetch(url);
  // 404 = not a founder. We deliberately render a generic not-found below
  // rather than leak the existence of /internal routes to non-founders.
  if (res.status === 404) throw new Error("not_found");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

function formatUsd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(6)}`;
  if (value < 1) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toLocaleString();
}

export default function InternalCostsPage() {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("30d");

  const { data, error, isLoading } = useSWR<LlmCostsResponse>(
    `/api/internal/llm-costs?period=${period}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  if (error?.message === "not_found") {
    return (
      <div className="mx-auto max-w-md p-12 text-center text-sm text-muted-foreground">
        Page not found.
      </div>
    );
  }

  const maxDaily = Math.max(1, ...(data?.daily_cost.map((d) => d.cost_usd) ?? [0]));

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            LLM Cost Dashboard
            <span className="ml-2 align-middle text-xs font-normal text-muted-foreground">
              · founder only
            </span>
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Per-org token spend across every workspace. Flagged rows exceed our
            unprofitable threshold for the selected window.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border border-border bg-card p-0.5">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setPeriod(p.value)}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition-colors",
                period === p.value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </header>

      {/* Stat cards */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total cost"
          value={isLoading ? <Skeleton className="h-7 w-24" /> : formatUsd(data?.total_cost_usd ?? 0)}
          hint={
            data?.delta_pct == null
              ? "no comparable prior window"
              : `${data.delta_pct >= 0 ? "+" : ""}${data.delta_pct}% vs previous ${period}`
          }
        />
        <StatCard
          label="Avg cost / query"
          value={
            isLoading ? <Skeleton className="h-7 w-20" /> : formatUsd(data?.avg_cost_per_query_usd ?? 0)
          }
          hint={data ? `${data.total_queries.toLocaleString()} queries` : ""}
        />
        <StatCard
          label="Total tokens"
          value={isLoading ? <Skeleton className="h-7 w-20" /> : formatTokens(data?.total_tokens ?? 0)}
          hint="input + output combined"
        />
        <StatCard
          label="Orgs flagged"
          value={
            isLoading ? (
              <Skeleton className="h-7 w-12" />
            ) : (
              (data?.per_org ?? []).filter((o) => o.over_threshold).length
            )
          }
          hint="above unprofitable threshold"
        />
      </div>

      {/* Daily cost trend */}
      <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Daily spend</h2>
          {data?.daily_cost.length === 0 && (
            <span className="text-xs text-muted-foreground">no spend in window</span>
          )}
        </div>
        <div className="flex h-32 items-end gap-1">
          {(data?.daily_cost ?? []).map((d) => {
            const pct = Math.max(2, Math.round((d.cost_usd / maxDaily) * 100));
            return (
              <div
                key={d.date}
                className="group relative flex-1 rounded-t bg-primary/70 transition-colors hover:bg-primary"
                style={{ height: `${pct}%` }}
                title={`${d.date}: ${formatUsd(d.cost_usd)}`}
              />
            );
          })}
        </div>
      </section>

      {/* Per-org table */}
      <section className="rounded-xl border border-border bg-card shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">Cost per organization</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border text-left text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-2">Org</th>
                <th className="px-4 py-2">Plan</th>
                <th className="px-4 py-2 text-right">Queries</th>
                <th className="px-4 py-2 text-right">Tokens</th>
                <th className="px-4 py-2 text-right">Cost / query</th>
                <th className="px-4 py-2 text-right">Total cost</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-muted-foreground">
                    Loading…
                  </td>
                </tr>
              )}
              {!isLoading && (data?.per_org ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-muted-foreground">
                    No spend in this window.
                  </td>
                </tr>
              )}
              {(data?.per_org ?? []).map((row) => (
                <tr
                  key={row.org_id}
                  className={cn(
                    "border-b border-border last:border-0",
                    row.over_threshold && "bg-red-50 dark:bg-red-950/30",
                  )}
                >
                  <td className="px-4 py-2 font-medium">{row.name}</td>
                  <td className="px-4 py-2">
                    <Badge variant="outline" className="capitalize">
                      {row.plan}
                    </Badge>
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    {row.queries.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    {formatTokens(row.tokens)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    {formatUsd(row.cost_per_query_usd)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-sm font-semibold">
                    {formatUsd(row.cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Cost by model */}
      <section className="rounded-xl border border-border bg-card shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">Cost by model</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border text-left text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-2">Model</th>
                <th className="px-4 py-2 text-right">Queries</th>
                <th className="px-4 py-2 text-right">Tokens</th>
                <th className="px-4 py-2 text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {(data?.by_model ?? []).map((row) => (
                <tr key={row.model} className="border-b border-border last:border-0">
                  <td className="px-4 py-2 font-mono text-xs">{row.model}</td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    {row.queries.toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-xs">
                    {formatTokens(row.tokens)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-sm font-semibold">
                    {formatUsd(row.cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
