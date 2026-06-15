"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock,
  FileQuestion,
  Loader2,
  Sparkles,
  XCircle,
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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "@/lib/date";

interface KnowledgeGapItem {
  topic: string;
  count: number;
  last_asked: string;
  sample_queries: string[];
  draft: { draft_id: string; draft_status: string } | null;
}

interface KnowledgeGapsResponse {
  days: number;
  items: KnowledgeGapItem[];
  total_topics: number;
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const PERIODS: { label: string; value: number }[] = [
  { label: "7 days", value: 7 },
  { label: "30 days", value: 30 },
  { label: "90 days", value: 90 },
];

export default function KnowledgeGapsPage() {
  const params = useSearchParams();
  const focusDraftId = params.get("draft");
  const [period, setPeriod] = useState(30);
  const [activeDraftId, setActiveDraftId] = useState<string | null>(focusDraftId);

  const { data, error, isLoading, mutate } = useSWR<KnowledgeGapsResponse>(
    `/api/admin/knowledge-gaps?days=${period}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  // If the URL carried ?draft=…, open the review dialog automatically on
  // first mount. Wait for data so the dialog can find its associated topic
  // row to highlight.
  useEffect(() => {
    if (focusDraftId && data && !activeDraftId) {
      setActiveDraftId(focusDraftId);
    }
  }, [focusDraftId, data, activeDraftId]);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Knowledge gaps
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Topics your team asked about but your knowledge base couldn&apos;t
            answer. The AI drafts stub documents for the most-asked topics.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg bg-muted p-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setPeriod(p.value)}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                period === p.value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
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
        <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading gaps…
        </div>
      ) : data.items.length === 0 ? (
        <div className="rounded-xl border border-border bg-card p-10 text-center">
          <FileQuestion className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
          <p className="text-sm font-medium">No knowledge gaps in this window.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Either your team found answers in your knowledge base, or the chat
            volume is too low to detect a pattern. Both are good news.
          </p>
        </div>
      ) : (
        <GapsTable
          items={data.items}
          onOpenDraft={(id) => setActiveDraftId(id)}
        />
      )}

      {activeDraftId && (
        <DraftReviewDialog
          draftId={activeDraftId}
          onClose={() => setActiveDraftId(null)}
          onResolved={() => {
            setActiveDraftId(null);
            mutate();
          }}
        />
      )}
    </div>
  );
}

function GapsTable({
  items,
  onOpenDraft,
}: {
  items: KnowledgeGapItem[];
  onOpenDraft: (id: string) => void;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-2.5 font-medium">Topic</th>
            <th className="px-4 py-2.5 font-medium">Times asked</th>
            <th className="px-4 py-2.5 font-medium">Last asked</th>
            <th className="px-4 py-2.5 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const hasDraft = item.draft?.draft_status === "pending_review";
            const approved = item.draft?.draft_status === "approved";
            const rejected = item.draft?.draft_status === "rejected";
            return (
              <tr
                key={item.topic}
                className={cn(
                  "border-b border-border/50 last:border-0",
                  hasDraft && "bg-amber-500/5",
                )}
              >
                <td className="px-4 py-3">
                  <p className="font-medium">{item.topic}</p>
                  {item.sample_queries.length > 0 && (
                    <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                      e.g. &ldquo;{item.sample_queries[0]}&rdquo;
                    </p>
                  )}
                </td>
                <td className="px-4 py-3 font-mono tabular-nums">
                  {item.count}
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {formatDistanceToNow(item.last_asked)}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {hasDraft ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onOpenDraft(item.draft!.draft_id)}
                      className="gap-1"
                    >
                      <Sparkles className="h-3 w-3" />
                      Review AI draft
                      <ChevronRight className="h-3 w-3" />
                    </Button>
                  ) : approved ? (
                    <Badge
                      variant="outline"
                      className="gap-1 border-emerald-500/40 text-emerald-700 dark:text-emerald-300"
                    >
                      <CheckCircle2 className="h-3 w-3" />
                      Approved
                    </Badge>
                  ) : rejected ? (
                    <Badge
                      variant="outline"
                      className="gap-1 text-muted-foreground"
                    >
                      <XCircle className="h-3 w-3" />
                      Dismissed
                    </Badge>
                  ) : item.count < 3 ? (
                    <span className="text-xs text-muted-foreground">
                      Watching (drafts at 3+)
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      Drafting…
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

interface DraftDetail {
  id: string;
  title: string;
  content: string;
  status: string;
  gap_topic: string | null;
  gap_count: number | null;
  ingested_document_id: string | null;
}

function DraftReviewDialog({
  draftId,
  onClose,
  onResolved,
}: {
  draftId: string;
  onClose: () => void;
  onResolved: () => void;
}) {
  const [draft, setDraft] = useState<DraftDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/admin/document-drafts/${encodeURIComponent(draftId)}`,
        );
        if (!res.ok) {
          setLoadError(`Failed to load (${res.status})`);
          return;
        }
        const d = (await res.json()) as DraftDetail;
        if (cancelled) return;
        setDraft(d);
        setTitle(d.title);
        setContent(d.content);
      } catch {
        if (!cancelled) setLoadError("Network error.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [draftId]);

  const approve = async () => {
    setSubmitting("approve");
    try {
      const res = await fetch(
        `/api/admin/document-drafts/${encodeURIComponent(draftId)}/approve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, content }),
        },
      );
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        toast.error(payload.detail ?? "Approve failed.");
        return;
      }
      toast.success("Draft approved — ingestion started.");
      onResolved();
    } finally {
      setSubmitting(null);
    }
  };

  const reject = async () => {
    setSubmitting("reject");
    try {
      const res = await fetch(
        `/api/admin/document-drafts/${encodeURIComponent(draftId)}/reject`,
        { method: "POST" },
      );
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        toast.error(payload.detail ?? "Reject failed.");
        return;
      }
      toast.success("Draft dismissed.");
      onResolved();
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Review AI-drafted document</DialogTitle>
          {draft?.gap_topic ? (
            <DialogDescription>
              Generated because <strong>{draft.gap_topic}</strong> was asked
              about {draft.gap_count ?? 0} times with no answers in your
              knowledge base.
            </DialogDescription>
          ) : null}
        </DialogHeader>

        {loadError ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
            {loadError}
          </div>
        ) : !draft ? (
          <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading draft…
          </div>
        ) : draft.status !== "pending_review" ? (
          <div className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
            This draft has already been{" "}
            <strong>{draft.status === "approved" ? "approved" : "dismissed"}</strong>.
          </div>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="draft-title">Title</Label>
              <Input
                id="draft-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="draft-content">Content</Label>
              <Textarea
                id="draft-content"
                rows={16}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                className="font-mono text-xs"
              />
              <p className="text-[11px] text-muted-foreground">
                Look for <code className="rounded bg-muted px-1">[NEEDS INPUT: …]</code> markers — those are the gaps the AI couldn&apos;t fill from existing context.
              </p>
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="ghost"
            onClick={reject}
            disabled={!!submitting || !draft || draft.status !== "pending_review"}
          >
            {submitting === "reject" ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <XCircle className="mr-2 h-3.5 w-3.5" />
            )}
            Dismiss
          </Button>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button
            onClick={approve}
            disabled={
              !!submitting ||
              !draft ||
              draft.status !== "pending_review" ||
              title.trim().length === 0 ||
              content.trim().length === 0
            }
          >
            {submitting === "approve" ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="mr-2 h-3.5 w-3.5" />
            )}
            Approve &amp; ingest
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
