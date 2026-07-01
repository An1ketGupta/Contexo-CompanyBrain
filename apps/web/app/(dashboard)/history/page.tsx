"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { History, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  useQueryHistory,
  type QueryHistoryEntry,
} from "@/hooks/use-query-history";
import { Skeleton } from "@/components/ui/skeleton";

const INTENT_LABEL: Record<string, string> = {
  factual_qa: "Q&A",
  task_generation: "Writing",
  analysis: "Analysis",
  search: "Search",
  summarization: "Summary",
  comparison: "Compare",
  generic: "General",
};

// Actual accent tints — one hue per intent, drawn from the design system's
// status palette (blue/violet/amber/green/pink) rather than raw Tailwind.
const INTENT_COLOR: Record<string, string> = {
  factual_qa: "bg-brand-tint text-brand",
  task_generation: "bg-violet-tint text-violet",
  analysis: "bg-amber-tint text-amber",
  search: "bg-success-tint text-success",
  summarization: "bg-brand-tint text-brand",
  comparison: "bg-pink-tint text-pink",
  generic: "bg-muted text-muted-foreground",
};

const INTENT_FILTERS: { value: string; label: string }[] = [
  { value: "factual_qa", label: "Q&A" },
  { value: "task_generation", label: "Writing" },
  { value: "analysis", label: "Analysis" },
  { value: "search", label: "Search" },
  { value: "summarization", label: "Summary" },
];

export default function QueryHistoryPage() {
  const [intent, setIntent] = useState<string | null>(null);
  const [search, setSearch] = useState<string>("");

  // Debounce the search input lightly so each keystroke doesn't fire a fetch.
  // Must be an effect, not useMemo — it schedules a timer (a side effect) and
  // touches `window`, so running it during SSR would throw "window is not
  // defined". useEffect only runs client-side, which is exactly what we want.
  const [debouncedSearch, setDebouncedSearch] = useState<string>("");
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => window.clearTimeout(t);
  }, [search]);

  const { entries, hasMore, loading, error, loadMore } = useQueryHistory({
    intent,
    search: debouncedSearch || null,
  });

  return (
    <div className="mx-auto max-w-3xl p-6 md:p-8">
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
            Query history
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your recent questions and the conversations they belong to.
          </p>
        </div>
        <div className="rounded-xl bg-brand-tint p-2.5 text-brand">
          <History className="h-4 w-4" />
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your questions…"
            className="w-full rounded-[10px] border border-input bg-background py-2 pl-8 pr-3 text-sm transition-colors placeholder:text-muted-foreground focus:border-ring focus:outline-none focus:ring-2 focus:ring-ring/25"
          />
        </div>
        <button
          type="button"
          onClick={() => setIntent(null)}
          className={cn(
            "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
            intent === null
              ? "border-transparent bg-brand-tint font-semibold text-brand"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          All
        </button>
        {INTENT_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setIntent(intent === f.value ? null : f.value)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
              intent === f.value
                ? "border-transparent bg-brand-tint font-semibold text-brand"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading && entries.length === 0 ? (
        <ListSkeleton />
      ) : entries.length === 0 ? (
        <EmptyState filtered={Boolean(intent || debouncedSearch)} />
      ) : (
        <div className="space-y-2">
          {entries.map((entry) => (
            <HistoryRow key={entry.id} entry={entry} />
          ))}
          {hasMore && (
            <div className="pt-2 text-center">
              <button
                type="button"
                onClick={() => void loadMore()}
                disabled={loading}
                className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                {loading ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function HistoryRow({ entry }: { entry: QueryHistoryEntry }) {
  const label = entry.intent
    ? INTENT_LABEL[entry.intent] ?? entry.intent
    : null;
  const colorClass = entry.intent
    ? INTENT_COLOR[entry.intent] ?? INTENT_COLOR.generic
    : INTENT_COLOR.generic;

  const inner = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="line-clamp-2 flex-1 text-sm text-foreground">
          {entry.query_text}
        </p>
        {label && (
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide",
              colorClass,
            )}
          >
            {label}
          </span>
        )}
      </div>
      <div className="mt-1.5 flex items-center gap-3 text-xs text-muted-foreground">
        <span>{formatRelativeTime(entry.created_at)}</span>
        {entry.source_count > 0 && (
          <span>
            {entry.source_count} source{entry.source_count === 1 ? "" : "s"}
          </span>
        )}
        {entry.tool_calls > 0 && (
          <span>
            {entry.tool_calls} search{entry.tool_calls === 1 ? "" : "es"}
          </span>
        )}
        {entry.latency_ms !== null && (
          <span>{Math.round(entry.latency_ms / 100) / 10}s</span>
        )}
      </div>
    </>
  );

  // Deep-link to the conversation. Falls back to a non-clickable row if the
  // conversation has been deleted (FK was SET NULL).
  if (entry.conversation_id) {
    return (
      <Link
        href={`/chat/${entry.conversation_id}`}
        className="block rounded-xl border border-border bg-background px-4 py-3 transition-colors hover:border-input hover:bg-muted"
      >
        {inner}
      </Link>
    );
  }
  return (
    <div className="rounded-xl border border-dashed border-border bg-background px-4 py-3 opacity-70">
      {inner}
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="rounded-xl border border-border bg-background px-4 py-3"
        >
          <Skeleton className="h-3.5 w-3/4" />
          <Skeleton className="mt-2 h-2.5 w-1/3" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-background px-6 py-16 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
        <History className="h-5 w-5" />
      </div>
      <h2 className="text-base font-semibold text-foreground">
        {filtered ? "No matching queries" : "No questions yet"}
      </h2>
      <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
        {filtered
          ? "Try clearing the filters or your search."
          : "Once you start chatting, your questions show up here so you can pick up where you left off."}
      </p>
    </div>
  );
}

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}
