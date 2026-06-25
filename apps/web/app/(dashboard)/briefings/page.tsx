"use client";

import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, Calendar, ChevronRight, Sparkles } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";

interface BriefingRow {
  id: string;
  status: "generating" | "ok" | "failed";
  summary: string | null;
  period_key: string;
  created_at: string;
  delivered_email_at: string | null;
  delivered_inapp_at: string | null;
}

interface ListResponse {
  briefings: BriefingRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function BriefingsIndexPage() {
  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/briefings",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">
          Weekly briefings
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          A Monday-morning snapshot of what needs your attention. Tune the
          schedule and delivery from{" "}
          <Link href="/settings" className="font-medium text-primary hover:underline">
            settings
          </Link>
          .
        </p>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Could not load briefings. Refresh to retry.</span>
        </div>
      ) : null}

      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20 w-full rounded-lg" />
          ))}
        </div>
      ) : !data || data.briefings.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="space-y-2">
          {data.briefings.map((b) => (
            <li key={b.id}>
              <Link
                href={`/briefings/${b.id}`}
                className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 transition-colors hover:border-foreground/30"
              >
                <Calendar className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {b.period_key} ·{" "}
                    <span className="font-normal text-muted-foreground">
                      {formatDistanceToNow(b.created_at)} ago
                    </span>
                  </p>
                  {b.summary && (
                    <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                      {b.summary}
                    </p>
                  )}
                </div>
                {b.status === "generating" && (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                    generating
                  </span>
                )}
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-12 text-center">
      <Sparkles className="mx-auto h-8 w-8 text-muted-foreground" />
      <h3 className="mt-3 text-sm font-medium">No briefings yet</h3>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
        Your first briefing arrives on Monday morning. Turn it on or change
        the schedule in settings.
      </p>
      <Link
        href="/settings"
        className="mt-4 inline-block text-xs font-medium text-primary hover:underline"
      >
        Open briefing settings →
      </Link>
    </div>
  );
}
