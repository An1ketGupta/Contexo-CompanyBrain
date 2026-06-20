"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  Check,
  Loader2,
  ShieldAlert,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";

interface MentionRow {
  id: string;
  source_kind: "chat" | "agent";
  message_id: string | null;
  agent_run_id: string | null;
  conversation_id: string | null;
  user_id: string | null;
  matched_term: string;
  watchlist_source: "org" | "user";
  snippet: string;
  match_count: number;
  status: "open" | "dismissed";
  dismissed_by: string | null;
  dismissed_at: string | null;
  created_at: string;
}

interface MentionsResponse {
  items: MentionRow[];
  total: number;
  limit: number;
  offset: number;
  summary: {
    open_total: number;
    by_term: { term: string; count: number }[];
  };
}

type StatusFilter = "open" | "dismissed" | "all";
type SourceFilter = "" | "chat" | "agent";

const fetcher = async (url: string): Promise<MentionsResponse> => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "dismissed", label: "Dismissed" },
  { value: "all", label: "All" },
];

export default function CompetitorMentionsPage() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("");
  const [termFilter, setTermFilter] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<string | null>(null);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set("status", statusFilter);
    params.set("limit", "100");
    if (sourceFilter) params.set("source", sourceFilter);
    if (termFilter) params.set("term", termFilter);
    return params.toString();
  }, [statusFilter, sourceFilter, termFilter]);

  const { data, error, isLoading, mutate } = useSWR<MentionsResponse>(
    `/api/admin/competitor-mentions?${queryString}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const items = data?.items ?? [];
  const openTotal = data?.summary.open_total ?? 0;
  const topTerms = data?.summary.by_term ?? [];

  const allSelected =
    items.length > 0 &&
    items.every((it) => it.status === "dismissed" || selected.has(it.id));
  const openIds = items.filter((it) => it.status === "open").map((it) => it.id);

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => {
      if (openIds.every((id) => prev.has(id))) {
        const next = new Set(prev);
        for (const id of openIds) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of openIds) next.add(id);
      return next;
    });
  };

  const dismiss = async (
    payload: { ids?: string[]; term?: string },
    busyKey: string,
  ) => {
    setBulkBusy(busyKey);
    try {
      const res = await fetch("/api/admin/competitor-mentions/dismiss", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      const out = (await res.json()) as { dismissed: number };
      toast.success(
        `Dismissed ${out.dismissed} mention${out.dismissed === 1 ? "" : "s"}.`,
      );
      setSelected(new Set());
      await mutate();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Couldn't dismiss mentions.",
      );
    } finally {
      setBulkBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-amber-600" />
          <h1 className="text-xl font-semibold tracking-tight">
            Competitor mentions
          </h1>
        </div>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Every assistant or agent output where a watchlist term appeared.
          Review, then dismiss when you&apos;re comfortable the mention was
          benign. {openTotal > 0 && (
            <span className="font-medium text-foreground">
              {openTotal} open
            </span>
          )}
        </p>
      </header>

      {topTerms.length > 0 && (
        <section className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Top mentioned terms (open)
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {topTerms.map(({ term, count }) => (
              <div
                key={term}
                className="inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100"
              >
                <span className="font-medium">{term}</span>
                <span className="text-amber-700 dark:text-amber-300">
                  ×{count}
                </span>
                <button
                  type="button"
                  onClick={() => dismiss({ term }, `term:${term}`)}
                  disabled={bulkBusy !== null}
                  className="ml-1 rounded p-0.5 text-amber-700 transition-colors hover:bg-amber-100 disabled:opacity-50 dark:text-amber-300 dark:hover:bg-amber-500/20"
                  title={`Dismiss all open mentions of "${term}"`}
                  aria-label={`Dismiss all open mentions of "${term}"`}
                >
                  {bulkBusy === `term:${term}` ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1 rounded-lg bg-muted p-1">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              onClick={() => {
                setStatusFilter(f.value);
                setSelected(new Set());
              }}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                statusFilter === f.value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value as SourceFilter)}
          className="rounded-md border border-input bg-background px-2.5 py-1.5 text-xs"
        >
          <option value="">All sources</option>
          <option value="chat">Chat</option>
          <option value="agent">Agent</option>
        </select>

        <input
          value={termFilter}
          onChange={(e) => setTermFilter(e.target.value)}
          placeholder="Filter by term…"
          className="rounded-md border border-input bg-background px-2.5 py-1.5 text-xs"
        />

        {statusFilter === "open" && selected.size > 0 && (
          <button
            type="button"
            onClick={() =>
              dismiss({ ids: Array.from(selected) }, "bulk:selected")
            }
            disabled={bulkBusy !== null}
            className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-50"
          >
            {bulkBusy === "bulk:selected" ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Check className="h-3 w-3" />
            )}
            Dismiss {selected.size} selected
          </button>
        )}
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <ul className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <li
              key={i}
              className="rounded-lg border border-border bg-card px-4 py-3"
            >
              <Skeleton className="h-5 w-1/3" />
              <Skeleton className="mt-2 h-3 w-full" />
              <Skeleton className="mt-1 h-3 w-2/3" />
            </li>
          ))}
        </ul>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-border bg-card px-4 py-12 text-center text-sm text-muted-foreground">
          No mentions
          {statusFilter !== "all" ? ` matching "${statusFilter}"` : ""} yet.
        </p>
      ) : (
        <>
          {statusFilter === "open" && openIds.length > 0 && (
            <label className="ml-1 flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                className="h-3.5 w-3.5"
              />
              Select all open on this page
            </label>
          )}
          <ul className="space-y-2">
            {items.map((row) => (
              <li
                key={row.id}
                className={cn(
                  "rounded-lg border bg-card px-4 py-3",
                  row.status === "dismissed"
                    ? "border-border opacity-70"
                    : "border-amber-200/70 dark:border-amber-500/30",
                )}
              >
                <div className="flex items-start gap-3">
                  {row.status === "open" && (
                    <input
                      type="checkbox"
                      checked={selected.has(row.id)}
                      onChange={() => toggleOne(row.id)}
                      className="mt-1 h-3.5 w-3.5"
                      aria-label={`Select mention of ${row.matched_term}`}
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">
                        {row.matched_term}
                      </span>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
                        {row.watchlist_source === "org"
                          ? "Workspace list"
                          : "Personal list"}
                      </span>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
                        {row.source_kind === "chat" ? "Chat" : "Agent"}
                      </span>
                      {row.match_count > 1 && (
                        <span className="text-muted-foreground">
                          ×{row.match_count}
                        </span>
                      )}
                      <span className="ml-auto text-muted-foreground">
                        {formatDistanceToNow(row.created_at)}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-3 whitespace-pre-wrap rounded bg-muted/50 px-2 py-1.5 font-mono text-[11px] leading-5">
                      {row.snippet}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                      {row.conversation_id && (
                        <a
                          href={`/chat/${row.conversation_id}`}
                          className="font-medium text-primary hover:underline"
                        >
                          Open conversation →
                        </a>
                      )}
                      {row.agent_run_id && (
                        <a
                          href={`/admin/agent-runs?run_id=${row.agent_run_id}`}
                          className="font-medium text-primary hover:underline"
                        >
                          Open agent run →
                        </a>
                      )}
                      {row.status === "dismissed" && row.dismissed_at && (
                        <span>
                          Dismissed {formatDistanceToNow(row.dismissed_at)}
                        </span>
                      )}
                    </div>
                  </div>
                  {row.status === "open" && (
                    <button
                      type="button"
                      onClick={() => dismiss({ ids: [row.id] }, `row:${row.id}`)}
                      disabled={bulkBusy !== null}
                      className="shrink-0 rounded-md border border-input bg-background px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
                    >
                      {bulkBusy === `row:${row.id}` ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        "Dismiss"
                      )}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
