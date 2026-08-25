"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  MessageSquare,
  Mic,
  Pencil,
  RefreshCw,
  Tag as TagIcon,
} from "lucide-react";
import { toast } from "sonner";
import { formatAbsolute, formatRelativeShort } from "@/lib/date";
import type { Document } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";
import { FileIcon } from "./file-icon";
import { StatusBadge } from "./status-badge";
import { HealthBadge } from "./health-badge";
import { DeleteDocumentDialog } from "./delete-document-dialog";
import { ShareTranscriptDialog } from "./share-transcript-dialog";
import { ProcessingIndicator } from "./processing-indicator";
import { TagDialog } from "./tag-dialog";
import { UploadVersionButton } from "./upload-version-button";
import { DocumentSummary } from "./document-summary";
import { TableOfContents } from "./table-of-contents";
import { DocumentVersionHistory } from "./version-history";
import { VersionDiffSection } from "./version-diff-section";
import { hasInsights, readDocumentInsights } from "./document-metadata";
import { useCurrentUser } from "@/hooks/use-user";

interface DocumentTableProps {
  documents: Document[];
  selectedIds: Set<string>;
  onSelectionChange: (next: Set<string>) => void;
  onDelete: (id: string) => Promise<void>;
  onRetry: (id: string) => Promise<void>;
  onUpdateTags: (id: string, tags: string[]) => Promise<void>;
  onUpdateVisibility: (id: string, visibility: "private" | "org") => Promise<void>;
  onRefresh?: () => void;
  hideVersioning?: boolean;
  hideTags?: boolean;
}

type EmbeddingStats = { embedded: number; failed: number; total: number };

function extractEmbeddingStats(metadata: unknown): EmbeddingStats | null {
  if (!metadata || typeof metadata !== "object") return null;
  const m = (metadata as Record<string, unknown>).embedding;
  if (!m || typeof m !== "object") return null;
  const e = m as Record<string, unknown>;
  const embedded = Number(e.embedded ?? NaN);
  const failed = Number(e.failed ?? NaN);
  const total = Number(e.total ?? NaN);
  if (Number.isNaN(embedded) || Number.isNaN(failed) || Number.isNaN(total)) return null;
  return { embedded, failed, total };
}

function hasFailedChunks(metadata: unknown): boolean {
  const s = extractEmbeddingStats(metadata);
  return !!s && s.failed > 0;
}

// Google Meet auto-sync, a legacy transcript source, or a manually uploaded VTT/Teams-JSON/
// Google Meet .txt transcript (file_type) — both route into MeetingNotesAgent
// server-side. Mirrors the backend predicate in routers/documents.py
// (DocumentKind filter).
export function isMeetingTranscript(doc: Document): boolean {
  return (
    doc.source === "zoom" ||
    doc.source === "google_meet_transcript" ||
    doc.file_type === "vtt" ||
    doc.file_type === "teams_transcript" ||
    doc.file_type === "transcript"
  );
}

function extractErrorReason(metadata: unknown): string | null {
  if (!metadata || typeof metadata !== "object") return null;
  const v = (metadata as Record<string, unknown>).error_reason;
  if (typeof v !== "string" || !v.trim()) return null;
  return prettifyErrorReason(v.trim());
}

// Strip Python-style traceback prefixes ("Ingestion failed: ValueError: …",
// "Could not parse document: …") so the user sees a sentence, not a stacktrace.
function prettifyErrorReason(raw: string): string {
  let msg = raw
    .replace(/^Ingestion failed:\s*[A-Za-z_]+Error:\s*/i, "")
    .replace(/^Ingestion failed:\s*/i, "")
    .replace(/^Could not parse document:\s*/i, "");
  msg = msg.charAt(0).toUpperCase() + msg.slice(1);
  if (!/[.!?]$/.test(msg)) msg += ".";
  return msg;
}

export function DocumentTable({
  documents,
  selectedIds,
  onSelectionChange,
  onDelete,
  onRetry,
  onUpdateTags,
  onUpdateVisibility,
  onRefresh,
  hideVersioning = false,
  hideTags = false,
}: DocumentTableProps) {
  const allSelected =
    documents.length > 0 && documents.every((d) => selectedIds.has(d.id));
  const someSelected =
    documents.some((d) => selectedIds.has(d.id)) && !allSelected;

  const toggleAll = (checked: boolean) => {
    if (checked) {
      const next = new Set(selectedIds);
      documents.forEach((d) => next.add(d.id));
      onSelectionChange(next);
    } else {
      const next = new Set(selectedIds);
      documents.forEach((d) => next.delete(d.id));
      onSelectionChange(next);
    }
  };

  const toggleOne = (id: string, checked: boolean) => {
    const next = new Set(selectedIds);
    if (checked) next.add(id);
    else next.delete(id);
    onSelectionChange(next);
  };

  // Clean up selection when documents change so a filter that hides a row
  // doesn't leave a phantom checked id in the bulk-action bar count.
  useEffect(() => {
    if (selectedIds.size === 0) return;
    const visible = new Set(documents.map((d) => d.id));
    let dirty = false;
    const next = new Set<string>();
    for (const id of selectedIds) {
      if (visible.has(id)) next.add(id);
      else dirty = true;
    }
    if (dirty) onSelectionChange(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documents]);

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-background">
      <table className="w-full text-sm">
        <thead className="border-b border-border bg-muted/40 font-mono text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
          <tr>
            <th className="w-8 px-3 py-2.5">
              <Checkbox
                checked={allSelected}
                indeterminate={someSelected}
                onCheckedChange={toggleAll}
                aria-label={
                  allSelected ? "Deselect all documents" : "Select all documents"
                }
              />
            </th>
            <th className="w-7 px-1 py-2.5">
              <span className="sr-only">Expand</span>
            </th>
            <th className="px-4 py-2.5 text-left font-medium">Name</th>
            <th className="hidden w-28 px-4 py-2.5 text-left font-medium lg:table-cell">
              Added
            </th>
            <th className="w-44 px-4 py-2.5 text-left font-medium">Status</th>
            <th className="px-4 py-2.5 text-right font-medium">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {documents.map((doc) => (
            <Row
              key={doc.id}
              doc={doc}
              selected={selectedIds.has(doc.id)}
              onSelect={(checked) => toggleOne(doc.id, checked)}
              onDelete={onDelete}
              onRetry={onRetry}
              onUpdateTags={onUpdateTags}
              onUpdateVisibility={onUpdateVisibility}
              onRefresh={onRefresh}
              hideVersioning={hideVersioning}
              hideTags={hideTags}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({
  doc,
  selected,
  onSelect,
  onDelete,
  onRetry,
  onUpdateTags,
  onUpdateVisibility,
  onRefresh,
  hideVersioning,
  hideTags,
}: {
  doc: Document;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onDelete: (id: string) => Promise<void>;
  onRetry: (id: string) => Promise<void>;
  onUpdateTags: (id: string, tags: string[]) => Promise<void>;
  onUpdateVisibility: (id: string, visibility: "private" | "org") => Promise<void>;
  onRefresh?: () => void;
  hideVersioning: boolean;
  hideTags: boolean;
}) {
  const [tagOpen, setTagOpen] = useState(false);
  const [savingTags, setSavingTags] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  const insights = readDocumentInsights(doc);
  const canExpand = doc.status === "ready";
  const hasExpandableInsights = hasInsights(insights);
  const maxPage = insights.toc.reduce<number | null>((max, entry) => {
    if (entry.page == null) return max;
    return max == null || entry.page > max ? entry.page : max;
  }, null);

  return (
    <>
    <tr
      className={cn(
        "group transition-colors",
        selected ? "bg-accent/60" : "hover:bg-muted/40",
      )}
    >
      <td className="w-8 px-3 py-3">
        <Checkbox
          checked={selected}
          onCheckedChange={onSelect}
          aria-label={`Select ${doc.name}`}
        />
      </td>
      <td className="w-7 px-1 py-3">
        <button
          type="button"
          onClick={() => canExpand && setExpanded((v) => !v)}
          disabled={!canExpand}
          aria-expanded={expanded}
          aria-label={
            !canExpand
              ? doc.status === "ready"
                ? "Summary generating…"
                : "Available once processing completes"
              : expanded
                ? `Hide ${doc.name} summary`
                : `Show ${doc.name} summary`
          }
          className={cn(
            "flex h-6 w-6 items-center justify-center rounded text-muted-foreground transition-colors",
            canExpand
              ? "hover:bg-muted hover:text-foreground"
              : "cursor-default opacity-30",
          )}
        >
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
        </button>
      </td>
      <td className="px-4 py-3">
        <div className="flex min-w-0 items-start gap-3">
          <FileIcon
            type={doc.file_type}
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
          />
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium text-foreground">{doc.name}</p>
            {isMeetingTranscript(doc) && (
              <div className="mt-1 flex items-center gap-1.5">
                <Badge variant="brand">
                  <Mic className="h-3 w-3" />
                  Meeting Transcript
                </Badge>
                <ShareTranscriptDialog
                  document={doc}
                  onUpdateVisibility={onUpdateVisibility}
                />
              </div>
            )}
            <div className="mt-1 flex min-w-0 items-center gap-1.5">
              {!hideVersioning && doc.current_version_number != null && (
                <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  v{doc.current_version_number}
                  {(doc.version_count ?? 0) > 1 ? ` of ${doc.version_count}` : ""}
                </span>
              )}
              {!hideTags && ((doc.tags ?? []).length === 0 ? (
                <button
                  type="button"
                  onClick={() => setTagOpen(true)}
                  className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/60 opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100"
                  aria-label={`Add tags to ${doc.name}`}
                >
                  <TagIcon className="h-3 w-3" />
                  Add tags
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setTagOpen(true)}
                  className="flex min-w-0 items-center gap-1.5 overflow-hidden rounded text-left focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  aria-label={`Edit tags for ${doc.name}`}
                >
                  {(doc.tags ?? []).slice(0, 3).map((t) => (
                    <span
                      key={t}
                      className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                    >
                      {t}
                    </span>
                  ))}
                  {(doc.tags ?? []).length > 3 && (
                    <span className="shrink-0 text-[10px] font-medium text-muted-foreground">
                      +{(doc.tags ?? []).length - 3}
                    </span>
                  )}
                  <Pencil className="h-3 w-3 shrink-0 text-muted-foreground/40 opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              ))}
              <span
                className="ml-auto shrink-0 text-[10px] tabular-nums text-muted-foreground/70 lg:hidden"
                title={formatAbsolute(doc.created_at)}
              >
                {formatRelativeShort(doc.created_at)}
              </span>
            </div>
          </div>
        </div>
      </td>
      <td
        className="hidden whitespace-nowrap px-4 py-3 text-xs tabular-nums text-muted-foreground lg:table-cell"
        title={formatAbsolute(doc.created_at)}
      >
        {formatRelativeShort(doc.created_at)}
      </td>
      <td className="px-4 py-3">
        {doc.status === "processing" ? (
          <ProcessingIndicator startedAt={doc.created_at} />
        ) : (
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge
                status={doc.status}
                embeddingStats={extractEmbeddingStats(doc.metadata)}
                errorReason={extractErrorReason(doc.metadata)}
              />
              {doc.status === "ready" && doc.health_label ? (
                <HealthBadge
                  label={doc.health_label}
                  score={doc.health_score ?? null}
                />
              ) : null}
            </div>
            {doc.status === "failed" && extractErrorReason(doc.metadata) && (
              <div
                className="flex max-w-[18rem] items-start gap-1.5 text-[11px] leading-snug text-destructive/80"
                title={extractErrorReason(doc.metadata) ?? undefined}
              >
                <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                <span className="line-clamp-2">
                  {extractErrorReason(doc.metadata)}
                </span>
              </div>
            )}
          </div>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        <div className="flex items-center justify-end gap-2">
          {doc.status === "ready" && (
            <Button asChild variant="ghost" size="sm">
              <Link
                href={`/chat?document_id=${encodeURIComponent(doc.id)}`}
                aria-label={`Ask questions about ${doc.name}`}
              >
                <MessageSquare className="h-3.5 w-3.5" />
                Ask
              </Link>
            </Button>
          )}
          {!hideVersioning && isAdmin && doc.status === "ready" && (
            <UploadVersionButton
              documentId={doc.id}
              documentName={doc.name}
              onUploaded={onRefresh}
            />
          )}
          {(doc.status === "failed" || hasFailedChunks(doc.metadata)) && (
            <RetryButton id={doc.id} name={doc.name} onRetry={onRetry} />
          )}
          <DeleteDocumentDialog document={doc} onConfirm={onDelete} />
        </div>
      </td>

      <TagDialog
        open={tagOpen}
        onOpenChange={setTagOpen}
        initial={doc.tags ?? []}
        title={`Tags for ${doc.name}`}
        submitLabel="Save tags"
        busy={savingTags}
        onSubmit={async (tags) => {
          setSavingTags(true);
          try {
            await onUpdateTags(doc.id, tags);
            toast.success("Tags updated.");
            setTagOpen(false);
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "Failed to save tags.");
          } finally {
            setSavingTags(false);
          }
        }}
      />
    </tr>
    {expanded && canExpand && (
      <tr className="bg-muted/20">
        <td colSpan={6} className="border-t border-border/60 px-0 py-0">
          <div className="mx-auto max-w-3xl space-y-6 px-6 py-6 md:px-10 md:py-7">
            <ExpandedHeader
              doc={doc}
              sectionCount={insights.toc.length}
              pageCount={maxPage}
            />

            {insights.summary && (
              <DocumentSummary
                summary={insights.summary}
                keyTopics={insights.keyTopics}
              />
            )}

            {insights.toc.length > 0 && (
              <section>
                <div className="mb-2 flex items-baseline justify-between">
                  <h4 className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                    Contents
                    <span className="ml-1.5 font-sans font-normal normal-case tracking-normal text-muted-foreground/60">
                      · {insights.toc.length}
                    </span>
                  </h4>
                </div>
                <div className="max-h-80 overflow-y-auto rounded-xl border border-border/60 bg-background p-2">
                  <TableOfContents entries={insights.toc} />
                </div>
              </section>
            )}

            {!hasExpandableInsights && (
              <p className="text-sm text-muted-foreground">
                Document insights are still generating.
                {!hideVersioning && " Version history is available below."}
              </p>
            )}

            {!hideVersioning && (
              <>
                <section className="space-y-2 rounded-xl border border-border bg-background p-3">
                  <div className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                    Version history
                  </div>
                  <DocumentVersionHistory documentId={doc.id} />
                </section>

                <VersionDiffSection documentId={doc.id} />
              </>
            )}
          </div>
        </td>
      </tr>
    )}
    </>
  );
}

function ExpandedHeader({
  doc,
  sectionCount,
  pageCount,
}: {
  doc: Document;
  sectionCount: number;
  pageCount: number | null;
}) {
  const parts: string[] = [];
  if (sectionCount > 0) {
    parts.push(`${sectionCount} section${sectionCount === 1 ? "" : "s"}`);
  }
  if (pageCount != null && pageCount > 0) {
    parts.push(`${pageCount} page${pageCount === 1 ? "" : "s"}`);
  }
  parts.push(`added ${formatRelativeShort(doc.created_at)}`);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-4">
      <p
        className="text-xs text-muted-foreground"
        title={formatAbsolute(doc.created_at)}
      >
        {parts.join(" · ")}
      </p>
      <Button asChild size="sm" variant="primary">
        <Link
          href={`/chat?document_id=${encodeURIComponent(doc.id)}`}
          aria-label={`Ask about ${doc.name}`}
        >
          <MessageSquare className="h-3.5 w-3.5" />
          Ask about this doc
        </Link>
      </Button>
    </div>
  );
}

function RetryButton({
  id,
  name,
  onRetry,
}: {
  id: string;
  name: string;
  onRetry: (id: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onRetry(id);
          toast.success(`Retrying "${name}"…`);
        } catch (err) {
          toast.error(err instanceof Error ? err.message : "Failed to retry.");
        } finally {
          setBusy(false);
        }
      }}
    >
      {busy ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : (
        <RefreshCw className="h-3 w-3" />
      )}
      Retry
    </Button>
  );
}
