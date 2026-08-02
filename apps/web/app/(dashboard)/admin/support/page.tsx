"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Inbox, Settings2 } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";

interface TicketRow {
  id: string;
  subject: string;
  from_email: string;
  from_name: string | null;
  status: string;
  category: string | null;
  priority: string | null;
  sentiment: string | null;
  created_at: string;
  updated_at: string;
  first_response_at: string | null;
}

interface TicketsResponse {
  tickets: TicketRow[];
  total: number;
}

type StatusFilter =
  | "all"
  | "pending_review"
  | "escalated"
  | "open"
  | "awaiting_customer"
  | "resolved";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "pending_review", label: "Needs review" },
  { value: "escalated", label: "Escalated" },
  { value: "open", label: "Open" },
  { value: "awaiting_customer", label: "Awaiting reply" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
];

const PRIORITY_STYLES: Record<string, string> = {
  p0: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  p1: "border-amber/30 bg-amber-tint text-amber-ink",
  p2: "border-border bg-muted text-muted-foreground",
  p3: "border-border bg-muted text-muted-foreground",
};

const SENTIMENT_STYLES: Record<string, string> = {
  negative: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  neutral: "border-border bg-muted text-muted-foreground",
  positive: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
};

const fetcher = async (url: string): Promise<TicketsResponse> => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function SupportTicketsPage() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending_review");

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set("limit", "100");
    if (statusFilter !== "all") params.set("status", statusFilter);
    return params.toString();
  }, [statusFilter]);

  const { data, error, isLoading } = useSWR<TicketsResponse>(
    `/api/admin/support?${queryString}`,
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 30_000 },
  );

  const tickets = data?.tickets ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Inbox className="h-5 w-5 text-muted-foreground" />
            <h1 className="text-2xl font-extrabold tracking-tight">Support Agent</h1>
          </div>
        </div>
        <Link
          href="/admin/support/settings"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs font-bold text-muted-foreground transition-colors hover:text-foreground"
        >
          <Settings2 className="h-3.5 w-3.5" />
          Settings
        </Link>
      </header>

      <div className="flex flex-wrap gap-1 rounded-xl bg-muted p-1">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setStatusFilter(f.value)}
            className={cn(
              "rounded-lg px-3 py-1.5 text-xs font-bold transition-colors",
              statusFilter === f.value
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600 dark:text-red-400">
          {error.message}
        </div>
      )}

      {isLoading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-2xl" />
          ))}
        </div>
      )}

      {!isLoading && !error && tickets.length === 0 && (
        <div className="rounded-2xl border border-border bg-card p-8 text-center">
          <p className="text-sm font-bold">No tickets here</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Inbound email classified as support will show up in this queue once
            the agent is enabled.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {tickets.map((t) => (
          <Link
            key={t.id}
            href={`/admin/support/${t.id}`}
            className="block rounded-2xl border border-border bg-card p-4 transition-colors hover:border-brand/40"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-bold">{t.subject}</div>
                <div className="mt-0.5 truncate text-xs text-muted-foreground">
                  {t.from_name ? `${t.from_name} · ` : ""}
                  {t.from_email}
                </div>
              </div>
              <div className="shrink-0 text-xs text-muted-foreground">
                {formatDistanceToNow(t.created_at)}
              </div>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] font-bold text-muted-foreground">
                {t.status.replace(/_/g, " ")}
              </span>
              {t.priority && (
                <span
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] font-bold uppercase",
                    PRIORITY_STYLES[t.priority] ?? PRIORITY_STYLES.p2,
                  )}
                >
                  {t.priority}
                </span>
              )}
              {t.sentiment && (
                <span
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] font-bold",
                    SENTIMENT_STYLES[t.sentiment] ?? SENTIMENT_STYLES.neutral,
                  )}
                >
                  {t.sentiment}
                </span>
              )}
              {t.category && (
                <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {t.category}
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
