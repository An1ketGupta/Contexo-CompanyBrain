"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  FileQuestion,
  Loader2,
  MessageSquareWarning,
  Sparkles,
  ThumbsDown,
} from "lucide-react";
import { toast } from "sonner";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
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
  suggested_doc: string | null;
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
  const wantsCreate = params.get("create") === "1";

  const [days, setDays] = useState<number>(
    PERIODS.find((p) => p.value === initialDays) ? initialDays : 7,
  );
  const [reason, setReason] = useState<FailureReason | null>(initialReason);
  const [activeItem, setActiveItem] = useState<FlaggedItem | null>(null);

  const qs = new URLSearchParams({ days: String(days) });
  if (reason) qs.set("reason", reason);

  const { data, error, isLoading, mutate } = useSWR<FlaggedResponse>(
    `/api/admin/feedback-flagged?${qs.toString()}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  // Auto-open the create dialog on first load when the email's CTA dropped
  // the user here with ?create=1. We pick the most recent flagged item
  // that has a suggested_doc as the seed.
  useEffect(() => {
    if (!wantsCreate || !data || activeItem) return;
    const seed = data.items.find((i) => i.suggested_doc);
    if (seed) setActiveItem(seed);
  }, [wantsCreate, data, activeItem]);

  const reasonCounts = data?.reasons ?? {};
  const totalCount = useMemo(
    () => Object.values(reasonCounts).reduce((a, b) => a + (b ?? 0), 0),
    [reasonCounts],
  );

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6 md:p-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">
            Flagged responses
          </h1>
          <p className="mt-1 max-w-xl text-sm text-muted-foreground">
            Messages your team thumbed-down, classified by failure mode. Open
            any to read in context — or turn the AI&apos;s suggested fix into
            a real document.
          </p>
        </div>
        <div className="flex shrink-0 gap-1 rounded-lg bg-muted p-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setDays(p.value)}
              className={cn(
                "whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                days === p.value
                  ? "bg-background text-foreground shadow-sm"
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
              : "border-border bg-background text-muted-foreground hover:text-foreground",
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
                  : "border-border bg-background text-muted-foreground hover:text-foreground",
              )}
            >
              {REASON_LABEL[r]} ({count})
            </button>
          );
        })}
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-lg" />
          ))}
        </div>
      ) : data.items.length === 0 ? (
        <div className="rounded-xl border border-border bg-card p-10 text-center">
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
            <FlaggedRow
              key={item.message_id}
              item={item}
              onCreateDoc={() => setActiveItem(item)}
            />
          ))}
        </ul>
      )}

      {activeItem ? (
        <CreateDocDialog
          item={activeItem}
          onClose={() => setActiveItem(null)}
          onCreated={() => {
            setActiveItem(null);
            mutate();
          }}
        />
      ) : null}
    </div>
  );
}

function FlaggedRow({
  item,
  onCreateDoc,
}: {
  item: FlaggedItem;
  onCreateDoc: () => void;
}) {
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
    <li className="overflow-hidden rounded-lg border border-border bg-card shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start justify-between gap-3 border-b border-border/60 bg-muted/30 px-4 py-2.5">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
          <ThumbsDown className="h-3.5 w-3.5 shrink-0 text-red-600" />
          <ReasonBadge reason={item.failure_reason} />
          <span className="text-muted-foreground">
            {formatDistanceToNow(item.created_at)}
          </span>
          {item.alerted_at ? (
            <Badge
              variant="outline"
              className="gap-1 border-amber-500/40 text-amber-700 dark:text-amber-300"
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
          {item.suggested_doc ? (
            <Button
              size="sm"
              onClick={onCreateDoc}
              className="h-7 gap-1 px-2.5 text-xs"
            >
              <Sparkles className="h-3 w-3" />
              Create doc
            </Button>
          ) : null}
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

        {item.suggested_doc ? (
          <div className="rounded-md border border-amber-500/30 bg-amber-50/60 p-3 dark:bg-amber-950/20">
            <div className="flex items-start gap-2">
              <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700 dark:text-amber-300" />
              <div className="min-w-0 flex-1">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-800 dark:text-amber-300">
                  Suggested fix
                </p>
                <p className="mt-1 text-sm text-foreground/90">
                  {item.suggested_doc}
                </p>
                <button
                  type="button"
                  onClick={onCreateDoc}
                  className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-amber-800 hover:underline dark:text-amber-300"
                >
                  Turn this into a document
                  <ExternalLink className="h-3 w-3" />
                </button>
              </div>
            </div>
          </div>
        ) : null}

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
    hallucination: "border-red-500/40 text-red-700 dark:text-red-300",
    outdated_policy: "border-amber-500/40 text-amber-700 dark:text-amber-300",
    missing_context: "border-blue-500/40 text-blue-700 dark:text-blue-300",
    wrong_tone: "border-purple-500/40 text-purple-700 dark:text-purple-300",
    wrong_format: "border-teal-500/40 text-teal-700 dark:text-teal-300",
    unknown: "text-muted-foreground",
  };
  return (
    <Badge variant="outline" className={cn("font-medium", palette[reason])}>
      {REASON_LABEL[reason]}
    </Badge>
  );
}

function CreateDocDialog({
  item,
  onClose,
  onCreated,
}: {
  item: FlaggedItem;
  onClose: () => void;
  onCreated: () => void;
}) {
  // Pre-fill: a sensible title built from the failure reason, and a
  // content body that reproduces the suggested fix, the user's actual
  // question, and the AI's flawed answer — so the admin has all the
  // raw material in one editable surface and isn't typing from scratch.
  const initialTitle = useMemo(() => {
    const suggested = item.suggested_doc?.trim() ?? "";
    if (suggested) {
      const first = suggested.split(/[.\n]/)[0]?.trim() ?? suggested;
      return first.slice(0, 120);
    }
    return `${REASON_LABEL[item.failure_reason]} fix: ${item.query.slice(0, 80)}`;
  }, [item]);

  const initialBody = useMemo(() => {
    const parts: string[] = [];
    if (item.suggested_doc) {
      parts.push(`# ${initialTitle}`);
      parts.push("");
      parts.push(item.suggested_doc.trim());
    } else {
      parts.push(`# ${initialTitle}`);
    }
    parts.push("");
    parts.push("---");
    parts.push("");
    parts.push(
      "<!-- Original context (delete before saving if not needed) -->",
    );
    if (item.query) parts.push(`**User asked:** ${item.query}`);
    parts.push("");
    parts.push(`**Previous AI response (flagged ${REASON_LABEL[item.failure_reason].toLowerCase()}):**`);
    parts.push("");
    parts.push(`> ${item.snippet}`);
    return parts.join("\n");
  }, [item, initialTitle]);

  const [title, setTitle] = useState(initialTitle);
  const [content, setContent] = useState(initialBody);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!title.trim() || !content.trim()) {
      toast.error("Title and content are required.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch("/api/admin/feedback-flagged/from-suggestion", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          content,
          source_message_ids: [item.message_id],
          failure_reason: item.failure_reason,
        }),
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as {
          detail?: string;
        };
        toast.error(payload.detail ?? `Failed to create doc (${res.status}).`);
        return;
      }
      toast.success("Document created — ingestion started.");
      onCreated();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create document from suggestion</DialogTitle>
          <DialogDescription>
            The AI suggested a document that would have improved this answer.
            Edit and save — once it&apos;s ingested, future chats on this topic
            will find it via hybrid search.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="fb-doc-title">Title</Label>
            <Input
              id="fb-doc-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={200}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fb-doc-content">Content (markdown)</Label>
            <Textarea
              id="fb-doc-content"
              rows={14}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="font-mono text-xs"
            />
            <p className="text-[11px] text-muted-foreground">
              The flagged message id is recorded with the doc so we can show
              which fixes resolved which complaints.
            </p>
          </div>
        </div>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="mr-2 h-3.5 w-3.5" />
            )}
            Create &amp; ingest
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
