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
import { Skeleton } from "@/components/ui/skeleton";
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
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">
            Knowledge gaps
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Topics your team asked about but your knowledge base couldn&apos;t
            answer. The AI drafts stub documents for the most-asked topics.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="rounded-full"
            onClick={() => {
              window.location.href = "/admin/knowledge-gaps/reports";
            }}
          >
            Weekly reports
          </Button>
          <div className="flex gap-1 rounded-xl bg-muted p-1">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              onClick={() => setPeriod(p.value)}
              className={cn(
                "rounded-lg px-3.5 py-1.5 text-xs font-bold transition-all",
                period === p.value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
          </div>
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="flex items-start gap-3 border-b border-border px-4 py-3.5 last:border-b-0"
            >
              <Skeleton className="h-7 w-7 shrink-0 rounded-md" />
              <div className="min-w-0 flex-1 space-y-1.5">
                <Skeleton
                  className="h-3.5"
                  style={{ width: `${45 + ((i * 13) % 40)}%` }}
                />
                <Skeleton className="h-2.5 w-1/3" />
              </div>
              <Skeleton className="h-5 w-12 rounded-full" />
              <Skeleton className="h-7 w-20 rounded-md" />
            </div>
          ))}
        </div>
      ) : data.items.length === 0 ? (
        <div className="rounded-2xl border border-border bg-card p-10 text-center">
          <FileQuestion className="mx-auto mb-2 h-8 w-8 text-muted-foreground" />
          <p className="text-sm font-bold">No knowledge gaps in this window.</p>
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
    <section className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[600px] text-sm">
        <thead>
          <tr className="border-b bg-muted/60 text-left font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            <th className="px-4 py-3">Topic</th>
            <th className="px-4 py-3">Times asked</th>
            <th className="px-4 py-3">Last asked</th>
            <th className="px-4 py-3">Status</th>
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
                  hasDraft && "bg-amber-tint/40",
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
                      className="gap-1 rounded-full"
                    >
                      <Sparkles className="h-3 w-3" />
                      Review AI draft
                      <ChevronRight className="h-3 w-3" />
                    </Button>
                  ) : approved ? (
                    <Badge variant="success" className="gap-1">
                      <CheckCircle2 className="h-3 w-3" />
                      Approved
                    </Badge>
                  ) : rejected ? (
                    <Badge variant="default" className="gap-1 text-muted-foreground">
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
      </div>
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
          <div className="rounded-lg border border-destructive/30 bg-destructive-soft p-3 text-xs text-destructive-ink">
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
