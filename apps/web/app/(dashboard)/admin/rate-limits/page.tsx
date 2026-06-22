"use client";

import useSWR from "swr";
import { AlertTriangle, Gauge } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface OrgQuota {
  plan: string;
  used: number;
  limit: number | null;
  unlimited: boolean;
  pct_used: number | null;
  reset_at: string;
  seconds_until_reset: number;
  projected_month_end: number;
  will_exceed: boolean;
  source: "redis" | "local";
}

interface PerUserRow {
  user_id: string;
  name: string;
  email: string | null;
  queries_30d: number;
  queries_today: number;
}

interface RateLimitsResponse {
  org_quota: OrgQuota;
  per_user: PerUserRow[];
}

const fetcher = async (url: string): Promise<RateLimitsResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function progressColor(pct: number | null): string {
  if (pct == null) return "bg-primary";
  if (pct >= 80) return "bg-red-500";
  if (pct >= 60) return "bg-amber-500";
  return "bg-primary";
}

export default function RateLimitsPage() {
  const { data, error, isLoading } = useSWR<RateLimitsResponse>(
    "/api/admin/rate-limits",
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 60_000 },
  );

  const quota = data?.org_quota;
  const pct = quota?.pct_used ?? 0;
  const shareDenom = quota?.limit || 1;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <header>
        <div className="flex items-center gap-2">
          <Gauge size={18} className="text-muted-foreground" />
          <h1 className="text-xl font-semibold tracking-tight">Rate limits & quota</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Your team&apos;s monthly AI usage against the {quota?.plan ?? ""} plan.
          {quota?.source === "local" ? " · live data temporarily unavailable" : null}
        </p>
      </header>

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          {error.message}
        </div>
      )}

      {/* Org quota card */}
      <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
        {isLoading || !quota ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Monthly query quota
                </p>
                <p className="mt-1 text-2xl font-semibold tabular-nums">
                  {quota.used.toLocaleString()}{" "}
                  <span className="text-base font-normal text-muted-foreground">
                    / {quota.unlimited ? "∞" : quota.limit?.toLocaleString()}
                  </span>
                </p>
              </div>
              {quota.will_exceed && (
                <Badge variant="destructive" className="gap-1">
                  <AlertTriangle size={12} />
                  Projected to exceed
                </Badge>
              )}
              {quota.unlimited && (
                <Badge variant="secondary">Unlimited</Badge>
              )}
            </div>

            {!quota.unlimited && (
              <>
                <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn("h-full rounded-full transition-all", progressColor(pct))}
                    style={{ width: `${Math.min(100, pct)}%` }}
                  />
                </div>
                <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                  <span>{pct}% used</span>
                  <span>Resets {formatDate(quota.reset_at)}</span>
                </div>
              </>
            )}

            {quota.will_exceed && (
              <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
                At your current run-rate, the team will use approximately{" "}
                <span className="font-semibold tabular-nums">
                  {quota.projected_month_end.toLocaleString()}
                </span>{" "}
                queries this month — over the {quota.limit?.toLocaleString()} limit
                for {quota.plan}.{" "}
                <a href="/settings" className="font-medium underline">
                  Upgrade plan →
                </a>
              </div>
            )}
          </>
        )}
      </section>

      {/* Per-user table */}
      <section className="rounded-xl border border-border bg-card shadow-sm">
        <div className="border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">Usage by team member</h2>
          <p className="text-xs text-muted-foreground">Last 30 days</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border text-left text-xs font-medium text-muted-foreground">
              <tr>
                <th className="px-4 py-2">User</th>
                <th className="px-4 py-2 text-right">Today</th>
                <th className="px-4 py-2 text-right">30 days</th>
                <th className="px-4 py-2 w-40">Share of quota</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">
                    Loading…
                  </td>
                </tr>
              )}
              {!isLoading && (data?.per_user ?? []).length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">
                    No queries in the last 30 days.
                  </td>
                </tr>
              )}
              {(data?.per_user ?? []).map((u) => {
                const share = Math.min(
                  100,
                  Math.round((u.queries_30d / shareDenom) * 100),
                );
                return (
                  <tr key={u.user_id} className="border-b border-border last:border-0">
                    <td className="px-4 py-2">
                      <p className="font-medium">{u.name}</p>
                      {u.email && (
                        <p className="text-xs text-muted-foreground">{u.email}</p>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs">
                      {u.queries_today.toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-xs">
                      {u.queries_30d.toLocaleString()}
                    </td>
                    <td className="px-4 py-2">
                      <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${share}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
