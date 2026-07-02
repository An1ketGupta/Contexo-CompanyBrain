"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  CircleX,
  FileText,
  Loader2,
  RefreshCw,
  Send,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { StatCard } from "@/components/admin/stat-card";
import { HealthBadge, type HealthLabel } from "@/components/documents/health-badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { formatDistanceToNow } from "@/lib/date";
import type { DocumentFileType } from "@/lib/types";

interface AtRiskDoc {
  id: string;
  name: string;
  file_type: DocumentFileType | null;
  health_score: number;
  health_label: HealthLabel | "unscored";
  last_accessed_at: string | null;
  age_days: number;
  citation_count: number;
  gap_flag_count: number;
  created_by: string | null;
  review_due_at: string | null;
  last_reviewed_at: string | null;
}

interface KnowledgeHealthResponse {
  counts: Record<HealthLabel | "unscored", number>;
  total: number;
  at_risk_docs: AtRiskDoc[];
  docs: AtRiskDoc[];
  label: string;
}

type BulkAction = "mark_for_review" | "request_owner_review" | "archive";

const fetcher = async (url: string): Promise<KnowledgeHealthResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const LABEL_ORDER: (HealthLabel | "unscored")[] = [
  "healthy",
  "stale",
  "at_risk",
  "unused",
];

const LABEL_TITLES: Record<HealthLabel | "unscored", string> = {
  healthy: "Healthy",
  stale: "Stale",
  at_risk: "At risk",
  unused: "Unused",
  unscored: "Unscored",
};

const FILTERS: { value: string; label: string }[] = [
  { value: "needs_attention", label: "Needs attention" },
  { value: "stale", label: "Stale" },
  { value: "at_risk", label: "At risk" },
  { value: "unused", label: "Unused" },
  { value: "all", label: "All" },
];

export default function KnowledgeHealthPage() {
  const [filter, setFilter] = useState<string>("needs_attention");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, error, isLoading, mutate } = useSWR<KnowledgeHealthResponse>(
    `/api/admin/knowledge-health?label=${filter}&limit=500`,
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-extrabold tracking-tight">
          Knowledge base health
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Which documents the AI is actually drawing on — and which look stale.
          Recomputed nightly.
        </p>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <KnowledgeHealthSkeleton />
      ) : (
        <KnowledgeHealthBody
          data={data}
          filter={filter}
          onFilter={(next) => {
            setFilter(next);
            setSelected(new Set());
          }}
          selected={selected}
          setSelected={setSelected}
          onRefresh={() => mutate()}
        />
      )}
    </div>
  );
}

function KnowledgeHealthSkeleton() {
  return (
    <>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="space-y-2.5 rounded-2xl border border-border bg-card p-5"
          >
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-8 w-12" />
            <Skeleton className="h-2.5 w-20" />
          </div>
        ))}
      </div>
      <section className="space-y-2">
        <Skeleton className="h-4 w-56" />
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3"
            >
              <Skeleton className="h-4 w-4 rounded" />
              <div className="min-w-0 flex-1 space-y-1.5">
                <Skeleton
                  className="h-3.5"
                  style={{ width: `${40 + ((i * 13) % 30)}%` }}
                />
                <Skeleton className="h-2.5 w-2/3" />
              </div>
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-3 w-12" />
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function KnowledgeHealthBody({
  data,
  filter,
  onFilter,
  selected,
  setSelected,
  onRefresh,
}: {
  data: KnowledgeHealthResponse;
  filter: string;
  onFilter: (next: string) => void;
  selected: Set<string>;
  setSelected: (next: Set<string>) => void;
  onRefresh: () => Promise<unknown>;
}) {
  const docs = data.docs ?? data.at_risk_docs ?? [];

  const allSelected = docs.length > 0 && docs.every((d) => selected.has(d.id));
  const someSelected = !allSelected && docs.some((d) => selected.has(d.id));

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(docs.map((d) => d.id)));
    }
  };

  const toggleOne = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  return (
    <>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {LABEL_ORDER.map((label) => (
          <StatCard
            key={label}
            label={LABEL_TITLES[label]}
            value={data.counts[label] ?? 0}
            hint="documents"
          />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Show
        </span>
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => onFilter(f.value)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
              filter === f.value
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border text-muted-foreground hover:border-foreground/30"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {docs.length === 0 ? (
        <p className="rounded-2xl border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          {filter === "needs_attention"
            ? "Every document looks healthy. Nothing to review."
            : "No documents match this filter."}
        </p>
      ) : (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">
              {docs.length} document{docs.length === 1 ? "" : "s"}
              {selected.size > 0 ? ` · ${selected.size} selected` : ""}
            </p>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Checkbox
                checked={allSelected}
                indeterminate={someSelected}
                onCheckedChange={toggleAll}
                aria-label="Select all"
              />
              Select all
            </label>
          </div>

          <div className="space-y-2">
            {docs.map((doc) => (
              <DocRow
                key={doc.id}
                doc={doc}
                selected={selected.has(doc.id)}
                onToggle={() => toggleOne(doc.id)}
              />
            ))}
          </div>
        </section>
      )}

      {selected.size > 0 && (
        <BulkActionBar
          selected={selected}
          onClear={() => setSelected(new Set())}
          onApplied={async () => {
            setSelected(new Set());
            await onRefresh();
          }}
        />
      )}
    </>
  );
}

function DocRow({
  doc,
  selected,
  onToggle,
}: {
  doc: AtRiskDoc;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={`flex items-center gap-3 rounded-xl border bg-card px-4 py-3 transition-colors ${
        selected ? "border-brand/40 bg-accent" : "border-border hover:bg-muted/40"
      }`}
    >
      <Checkbox
        checked={selected}
        onCheckedChange={onToggle}
        aria-label={`Select ${doc.name}`}
      />
      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{doc.name}</p>
        <p className="truncate text-xs text-muted-foreground">
          {doc.last_accessed_at
            ? `Last accessed ${formatDistanceToNow(doc.last_accessed_at)}`
            : "Never accessed"}{" "}
          · {doc.age_days} days old · {doc.citation_count} citations
          {doc.gap_flag_count > 0
            ? ` · ${doc.gap_flag_count} gap flag${doc.gap_flag_count === 1 ? "" : "s"}`
            : ""}
          {doc.review_due_at ? " · review due" : ""}
        </p>
      </div>
      <HealthBadge
        label={
          doc.health_label === "unscored"
            ? "healthy"
            : (doc.health_label as HealthLabel)
        }
        score={doc.health_score}
      />
      <Link
        href={`/documents?id=${encodeURIComponent(doc.id)}`}
        className="shrink-0 text-xs font-bold text-brand hover:underline"
      >
        Review →
      </Link>
    </div>
  );
}

function BulkActionBar({
  selected,
  onClear,
  onApplied,
}: {
  selected: Set<string>;
  onClear: () => void;
  onApplied: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<BulkAction | null>(null);
  const [confirm, setConfirm] = useState<BulkAction | null>(null);
  const ids = useMemo(() => Array.from(selected), [selected]);
  const count = ids.length;

  const apply = async (action: BulkAction) => {
    setBusy(action);
    try {
      const res = await fetch("/api/admin/knowledge-health/bulk-action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, document_ids: ids }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { message?: string };
        toast.error(data.message ?? "Bulk action failed.");
        return;
      }
      const data = (await res.json()) as {
        updated: number;
        skipped: number;
        notified: number;
      };
      const verb =
        action === "archive"
          ? "Archived"
          : action === "request_owner_review"
            ? "Notified owners for"
            : "Marked for review";
      const detail =
        action === "request_owner_review" && data.notified > 0
          ? ` (${data.notified} notification${data.notified === 1 ? "" : "s"} sent)`
          : "";
      toast.success(
        `${verb} ${data.updated} document${data.updated === 1 ? "" : "s"}${detail}.`,
      );
      await onApplied();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Bulk action failed.");
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  };

  return (
    <>
      <div className="sticky bottom-4 z-30 mx-auto flex w-fit max-w-full items-center gap-2 rounded-full border border-border bg-background/95 px-3 py-2 shadow-lg backdrop-blur">
        <button
          type="button"
          onClick={onClear}
          className="rounded-full p-1.5 text-muted-foreground hover:bg-muted"
          aria-label="Clear selection"
        >
          <X className="h-3.5 w-3.5" />
        </button>
        <span className="text-xs font-medium">
          {count} selected
        </span>
        <span className="mx-1 h-4 w-px bg-border" />
        <Button
          size="sm"
          variant="outline"
          disabled={busy !== null}
          onClick={() => void apply("mark_for_review")}
        >
          {busy === "mark_for_review" ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          )}
          Mark for review
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy !== null}
          onClick={() => void apply("request_owner_review")}
        >
          {busy === "request_owner_review" ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Send className="mr-1.5 h-3.5 w-3.5" />
          )}
          Notify owners
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="text-destructive hover:text-destructive"
          disabled={busy !== null}
          onClick={() => setConfirm("archive")}
        >
          <Archive className="mr-1.5 h-3.5 w-3.5" />
          Archive
        </Button>
      </div>

      <AlertDialog
        open={confirm === "archive"}
        onOpenChange={(v) => !v && setConfirm(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Archive {count} document{count === 1 ? "" : "s"}?</AlertDialogTitle>
            <AlertDialogDescription>
              They&apos;ll be hidden from search and won&apos;t be cited in new
              answers. You can restore them from the Documents page later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy !== null}>
              <CircleX className="mr-1.5 h-3.5 w-3.5" />
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => void apply("archive")}
              disabled={busy !== null}
            >
              <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
              Archive
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
