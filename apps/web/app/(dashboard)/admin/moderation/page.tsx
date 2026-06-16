"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Shield } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";

interface ModerationLog {
  id: string;
  query_text: string;
  result: "blocked" | "flagged";
  reason: string | null;
  action_taken: "rejected" | "logged_and_proceeded";
  created_at: string;
  user_id: string | null;
}

type Filter = "all" | "blocked" | "flagged";

const fetcher = async (url: string): Promise<ModerationLog[]> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "blocked", label: "Blocked" },
  { value: "flagged", label: "Flagged" },
];

export default function ModerationPage() {
  const [filter, setFilter] = useState<Filter>("all");

  const { data, error, isLoading } = useSWR<ModerationLog[]>(
    `/api/admin/moderation?result=${filter}&limit=100`,
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <header>
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-semibold tracking-tight">
            Content moderation
          </h1>
        </div>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Prompts the moderator caught. Blocked queries never reached the LLM;
          flagged queries proceeded with a log.
        </p>
      </header>

      <div className="flex gap-1 rounded-lg bg-muted p-1 w-fit">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFilter(f.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
              filter === f.value
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
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
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton
                    className="h-6"
                    style={{ width: `${50 + ((i * 11) % 35)}%` }}
                  />
                  <Skeleton className="h-2.5 w-2/5" />
                </div>
                <Skeleton className="h-5 w-16 rounded-full" />
              </div>
            </li>
          ))}
        </ul>
      ) : data.length === 0 ? (
        <p className="rounded-lg border border-border bg-card px-4 py-12 text-center text-sm text-muted-foreground">
          No moderation events
          {filter !== "all" ? ` matching "${filter}"` : ""} yet. Quiet is good.
        </p>
      ) : (
        <ul className="space-y-2">
          {data.map((log) => (
            <li
              key={log.id}
              className="rounded-lg border border-border bg-card px-4 py-3"
            >
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="rounded bg-muted px-2 py-1 font-mono text-xs">
                    {log.query_text}
                  </p>
                  {log.reason ? (
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      <span className="font-medium">Reason:</span> {log.reason}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <span
                    className={cn(
                      "rounded-full px-2 py-0.5 text-xs font-medium",
                      log.result === "blocked"
                        ? "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300"
                        : "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
                    )}
                  >
                    {log.result}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {formatDistanceToNow(log.created_at)}
                  </span>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
