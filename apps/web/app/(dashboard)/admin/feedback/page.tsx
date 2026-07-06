"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ExternalLink,
  FileQuestion,
  MessageSquareWarning,
  ThumbsDown,
} from "lucide-react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "@/lib/date";

type FailureReason =
  | "wrong_tone"
  | "missing_context"
  | "outdated_policy"
  | "hallucination"
  | "wrong_format"
  | "unknown";

interface FlaggedItem {
  message_id: string;
  conversation_id: string;
  snippet: string;
  query: string;
  sources: Array<{ document_name?: string; name?: string; title?: string }>;
  failure_reason: FailureReason;
  alerted_at: string | null;
  created_at: string;
}

interface FlaggedResponse {
  days: number;
  reason: FailureReason | null;
  items: FlaggedItem[];
  reasons: Partial<Record<FailureReason, number>>;
}

const REASON_LABEL: Record<FailureReason, string> = {
  wrong_tone: "Wrong tone",
  missing_context: "Missing context",
  outdated_policy: "Outdated policy",
  hallucination: "Hallucination",
  wrong_format: "Wrong format",
  unknown: "Unknown",
};

const PERIODS: { label: string; value: number }[] = [
  { label: "7 days", value: 7 },
  { label: "30 days", value: 30 },
  { label: "90 days", value: 90 },
];

const fetcher = async (url: string): Promise<FlaggedResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function AdminFeedbackPage() {
  const params = useSearchParams();
  const initialReason = params.get("reason") as FailureReason | null;
  const initialDays = Number(params.get("days") ?? "7") || 7;

  const [days, setDays] = useState<number>(
    PERIODS.find((p) => p.value === initialDays) ? initialDays : 7,
  );
  const [reason, setReason] = useState<FailureReason | null>(initialReason);

  const qs = new URLSearchParams({ days: String(days) });
  if (reason) qs.set("reason", reason);

  const { data, error, isLoading } = useSWR<FlaggedResponse>(
    `/api/admin/feedback-flagged?${qs.toString()}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const reasonCounts = data?.reasons ?? {};
  const totalCount = useMemo(
    () => Object.values(reasonCounts).reduce((a, b) => a + (b ?? 0), 0),
    [reasonCounts],
  );

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6 md:p-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-extrabold tracking-tight">
            Flagged responses
          </h1>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">
            Messages your team thumbed-down, classified by failure mode. Open
            any to read it in context.
          </p>
        </div>
        <div className="flex shrink-0 gap-1 rounded-xl bg-muted p-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setDays(p.value)}
              className={cn(
                "whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-bold transition-colors",
                days === p.value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </header>

      {/* Reason filter chips. Built from current-window counts so empty
          reasons disappear naturally. */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setReason(null)}
          className={cn(
            "whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors",
            reason === null
              ? "border-foreground bg-foreground text-background"
              : "border-border bg-card text-muted-foreground hover:text-foreground",
          )}
        >
          All ({totalCount})
        </button>
        {(Object.keys(REASON_LABEL) as FailureReason[]).map((r) => {
          const count = reasonCounts[r] ?? 0;
          if (count === 0 && reason !== r) return null;
          return (
            <button
              key={r}
              type="button"
              onClick={() => setReason(r)}
              className={cn(
                "whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                reason === r
                  ? "border-foreground bg-foreground text-background"
                  : "border-border bg-card text-muted-foreground hover:text-foreground",
              )}
            >
              {REASON_LABEL[r]} ({count})
            </button>
          );
        })}
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-2xl" />
          ))}
        </div>
      ) : data.items.length === 0 ? (
        <div className="rounded-2xl border border-border bg-card p-10 text-center">
          <FileQuestion className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
          <p className="text-sm font-medium">
            No analyzed negative feedback in this window.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Either your team didn&apos;t thumb-down anything, or the analyzer
            hasn&apos;t classified the new rows yet.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {data.items.map((item) => (
            <FlaggedRow key={item.message_id} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

function FlaggedRow({ item }: { item: FlaggedItem }) {
  // Snippets arrive as raw markdown from the chat pipeline. Rendering them
  // fully here would crowd the list, but leaving `**bold**` and `* bullet`
  // markers visible looks broken. Strip to a clean plain-text preview.
  const previewText = stripMarkdown(item.snippet);
  // Hybrid retrieval often surfaces multiple chunks from the same document;
  // a row of five identical "Code of Conduct.pdf" pills is noise. Dedupe by
  // normalized name, preserve first-seen order so the most-cited stays first.
  const sourceNames = dedupe(
    item.sources
      .map((s) => s.document_name || s.name || s.title || "")
      .filter(Boolean),
  );

  return (
    <li className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between gap-3 border-b border-border/60 bg-muted/30 px-4 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
          <ThumbsDown className="h-3.5 w-3.5 shrink-0 text-destructive" />
          <ReasonBadge reason={item.failure_reason} />
          <span className="text-muted-foreground">
            {formatDistanceToNow(item.created_at)}
          </span>
          {item.alerted_at ? (
            <Badge
              variant="outline"
              className="gap-1 border-amber/40 text-amber-ink"
            >
              <MessageSquareWarning className="h-3 w-3" />
              Tripped weekly alert
            </Badge>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button asChild size="sm" variant="ghost" className="h-7 px-2 text-xs">
            <Link
              href={`/chat/${item.conversation_id}#m-${item.message_id}`}
            >
              Open in chat
              <ExternalLink className="ml-1 h-3 w-3" />
            </Link>
          </Button>
        </div>
      </div>

      <div className="space-y-3 p-4">
        {item.query ? (
          <div>
            <SectionLabel>User asked</SectionLabel>
            <p className="mt-1 text-sm font-medium text-foreground">
              {item.query}
            </p>
          </div>
        ) : null}

        <div>
          <SectionLabel>AI response</SectionLabel>
          <p className="mt-1 line-clamp-3 text-sm text-muted-foreground">
            {previewText}
          </p>
        </div>

        {sourceNames.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-border/50 pt-3">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Sources used
            </span>
            {sourceNames.slice(0, 5).map((n) => (
              <Badge
                key={n}
                variant="outline"
                className="font-normal text-[11px]"
              >
                {n}
              </Badge>
            ))}
            {sourceNames.length > 5 ? (
              <span className="text-[11px] text-muted-foreground">
                +{sourceNames.length - 5} more
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
      {children}
    </p>
  );
}

// Drop the common markdown noise (bold/italic markers, list bullets, fenced
// code, headings) for at-a-glance previews. We don't try to be a full parser
// — the snippet is already capped at ~320 chars on the server.
function stripMarkdown(s: string): string {
  return s
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*\s][^*]*?)\*/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s{0,3}[-*+]\s+/gm, "")
    // Once newlines collapse, list bullets become orphan ` * ` runs in the
    // middle of the line — strip those too.
    .replace(/(^|\s)[-*+](?=\s)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function dedupe(xs: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const x of xs) {
    const key = x.trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(x);
  }
  return out;
}

function ReasonBadge({ reason }: { reason: FailureReason }) {
  const palette: Record<FailureReason, string> = {
    hallucination: "border-destructive/40 text-destructive-ink",
    outdated_policy: "border-amber/40 text-amber-ink",
    missing_context: "border-brand/40 text-brand",
    wrong_tone: "border-violet/40 text-violet",
    wrong_format: "border-success/40 text-success-ink",
    unknown: "text-muted-foreground",
  };
  return (
    <Badge variant="outline" className={cn("font-bold", palette[reason])}>
      {REASON_LABEL[reason]}
    </Badge>
  );
}
