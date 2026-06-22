"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Clock, Copy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCard } from "@/components/admin/stat-card";
import {
  DailyQueriesChart,
  IntentBreakdownChart,
} from "@/components/admin/analytics-charts";
import { formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";

interface TimeSavingsResponse {
  total_minutes_this_month: number;
  total_hours_this_month: number;
  per_user: {
    user_id: string;
    name: string;
    minutes: number;
    hours: number;
  }[];
}

interface TopCopiedMessage {
  message_id: string;
  conversation_id: string | null;
  conversation_title: string;
  copy_count: number;
  preview: string;
  intent: string | null;
}

interface AnalyticsResponse {
  period: "7d" | "30d" | "90d";
  stats: {
    total_queries: number;
    active_users: number;
    total_users: number;
    feedback_score: number | null;
    total_docs: number;
    docs_accessed: number;
    copy_rate: number | null;
  };
  daily_queries: { day: string; count: number }[];
  user_breakdown: {
    user_id: string;
    name: string;
    email: string | null;
    queries: number;
    last_active: string;
  }[];
  top_documents: { name: string; citations: number }[];
  top_copied: TopCopiedMessage[];
  intent_breakdown: { intent: string; count: number }[];
}

const PERIOD_OPTIONS: { label: string; value: "7d" | "30d" | "90d" }[] = [
  { label: "7 days", value: "7d" },
  { label: "30 days", value: "30d" },
  { label: "90 days", value: "90d" },
];

const fetcher = async (url: string): Promise<AnalyticsResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function AnalyticsPage() {
  const [period, setPeriod] = useState<"7d" | "30d" | "90d">("30d");

  const { data, error, isLoading } = useSWR<AnalyticsResponse>(
    `/api/admin/analytics?period=${period}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  // Time savings is its own endpoint and always reports last-30-days totals,
  // independent of the analytics period selector. Fetched separately so a
  // missing/failed call doesn't take down the rest of the page.
  const timeSavingsFetcher = async (url: string): Promise<TimeSavingsResponse> => {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Failed to load (${res.status})`);
    return res.json();
  };
  const { data: timeSavings } = useSWR<TimeSavingsResponse>(
    "/api/admin/time-savings",
    timeSavingsFetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Usage analytics
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            How your team is using Company Brain — and what they're getting back.
          </p>
        </div>
        <div className="flex shrink-0 gap-1 rounded-lg bg-muted p-1">
          {PERIOD_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setPeriod(opt.value)}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                period === opt.value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <AnalyticsSkeleton />
      ) : (
        <AnalyticsBody data={data} period={period} timeSavings={timeSavings} />
      )}
    </div>
  );
}

function AnalyticsBody({
  data,
  period,
  timeSavings,
}: {
  data: AnalyticsResponse;
  period: string;
  timeSavings: TimeSavingsResponse | undefined;
}) {
  const { stats, daily_queries, user_breakdown, top_documents, top_copied, intent_breakdown } =
    data;

  const adoption =
    stats.total_users > 0 ? stats.active_users / stats.total_users : null;
  const utilization =
    stats.total_docs > 0 ? stats.docs_accessed / stats.total_docs : null;

  return (
    <>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard
          label="Total queries"
          value={stats.total_queries.toLocaleString()}
          hint={`in the last ${period}`}
        />
        <StatCard
          label="Active users"
          value={stats.active_users}
          hint={
            stats.total_users
              ? `of ${stats.total_users} (${adoption !== null ? Math.round(adoption * 100) : 0}%)`
              : "no members yet"
          }
          progress={adoption}
        />
        <StatCard
          label="Satisfaction"
          value={
            stats.feedback_score === null ? "—" : `${stats.feedback_score}%`
          }
          hint={
            stats.feedback_score === null
              ? "no feedback yet"
              : "positive feedback rate"
          }
        />
        <StatCard
          label="Copy rate"
          value={stats.copy_rate === null ? "—" : `${stats.copy_rate}%`}
          hint={
            stats.copy_rate === null
              ? "no outputs copied yet"
              : "of outputs copied by users"
          }
          progress={stats.copy_rate !== null ? stats.copy_rate / 100 : null}
        />
        <StatCard
          label="Docs accessed"
          value={stats.docs_accessed}
          hint={
            stats.total_docs
              ? `of ${stats.total_docs} ready (${utilization !== null ? Math.round(utilization * 100) : 0}%)`
              : "no documents yet"
          }
          progress={utilization}
        />
      </div>

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="text-sm font-medium">Queries over time</h2>
          <p className="text-xs text-muted-foreground">last {period}</p>
        </div>
        {daily_queries.length === 0 ? (
          <EmptyChart label="No queries yet in this window." />
        ) : (
          <DailyQueriesChart data={daily_queries} />
        )}
      </section>

      <TimeSavedSection timeSavings={timeSavings} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-4 text-sm font-medium">Query types</h2>
          {intent_breakdown.length === 0 ? (
            <EmptyChart label="Not enough data yet." />
          ) : (
            <IntentBreakdownChart data={intent_breakdown} />
          )}
        </section>
        <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
          <h2 className="mb-4 text-sm font-medium">Most cited documents</h2>
          {top_documents.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted-foreground">
              No documents have been cited in this window.
            </p>
          ) : (
            <ul className="space-y-2">
              {top_documents.map((doc, i) => (
                <li
                  key={doc.name + i}
                  className="flex items-center gap-3 rounded-md py-1.5"
                >
                  <span className="w-5 shrink-0 text-xs font-mono text-muted-foreground">
                    {i + 1}.
                  </span>
                  <span className="flex-1 truncate text-sm">{doc.name}</span>
                  <Badge variant="outline">{doc.citations} cites</Badge>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {top_copied.length > 0 && (
        <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
          <div className="mb-4 flex items-center gap-1.5">
            <Copy className="h-3.5 w-3.5 text-muted-foreground" />
            <h2 className="text-sm font-medium">Most copied outputs</h2>
          </div>
          <ul className="space-y-2">
            {top_copied.map((msg, i) => (
              <li
                key={msg.message_id}
                className="flex items-start gap-3 rounded-md py-1.5"
              >
                <span className="w-5 shrink-0 pt-0.5 text-xs font-mono text-muted-foreground">
                  {i + 1}.
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-muted-foreground">
                    {msg.conversation_title}
                    {msg.intent && (
                      <span className="ml-1.5 rounded bg-muted px-1 py-0.5 text-[10px]">
                        {msg.intent}
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 text-sm text-foreground">{msg.preview}</p>
                </div>
                <Badge variant="outline" className="shrink-0 tabular-nums">
                  {msg.copy_count}×
                </Badge>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-medium">User breakdown</h2>
        {user_breakdown.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            No user activity in this window yet.
          </p>
        ) : (
          <div className="-mx-4 overflow-x-auto md:mx-0">
            <table className="w-full min-w-[480px] text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                  <th className="pb-2 font-medium">User</th>
                  <th className="pb-2 font-medium">Queries</th>
                  <th className="pb-2 font-medium">Last active</th>
                </tr>
              </thead>
              <tbody>
                {user_breakdown.map((u) => (
                  <tr
                    key={u.user_id}
                    className="border-b border-border/50 last:border-0"
                  >
                    <td className="py-2.5">
                      <p className="font-medium">{u.name}</p>
                      {u.email ? (
                        <p className="text-xs text-muted-foreground">
                          {u.email}
                        </p>
                      ) : null}
                    </td>
                    <td className="py-2.5 font-mono text-sm">{u.queries}</td>
                    <td className="py-2.5 text-xs text-muted-foreground">
                      {formatDistanceToNow(u.last_active)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function EmptyChart({ label }: { label: string }) {
  return (
    <p className="py-12 text-center text-sm text-muted-foreground">{label}</p>
  );
}

function TimeSavedSection({
  timeSavings,
}: {
  timeSavings: TimeSavingsResponse | undefined;
}) {
  return (
    <section className="rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-medium">
          <Clock className="h-3.5 w-3.5 text-muted-foreground" />
          Time saved
        </h2>
        <p className="text-xs text-muted-foreground">last 30 days</p>
      </div>
      {!timeSavings ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          Loading…
        </p>
      ) : timeSavings.total_minutes_this_month === 0 ? (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No measurable time saved yet — keep using Company Brain and this will fill in.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          <div className="md:col-span-1">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Org total
            </p>
            <p className="mt-1 text-3xl font-semibold tracking-tight tabular-nums">
              {timeSavings.total_hours_this_month}h
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {timeSavings.total_minutes_this_month.toLocaleString()} minutes
            </p>
          </div>
          <div className="md:col-span-2">
            <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Top contributors
            </p>
            {timeSavings.per_user.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No per-user attribution yet.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {timeSavings.per_user.slice(0, 5).map((u, i) => (
                  <li
                    key={u.user_id}
                    className="flex items-center gap-3 text-sm"
                  >
                    <span className="w-5 shrink-0 font-mono text-xs text-muted-foreground">
                      {i + 1}.
                    </span>
                    <span className="flex-1 truncate">{u.name}</span>
                    <Badge variant="outline" className="tabular-nums">
                      {u.hours}h
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function AnalyticsSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="space-y-2 rounded-lg border border-border bg-card p-4"
          >
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-8 w-16" />
            <Skeleton className="h-2.5 w-24" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div
            key={i}
            className="space-y-3 rounded-lg border border-border bg-card p-5"
          >
            <Skeleton className="h-4 w-36" />
            <Skeleton className="h-56 w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
