"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Loader2,
  MessageSquare,
  Pencil,
  RefreshCw,
  Tag as TagIcon,
} from "lucide-react";
import { toast } from "sonner";
import { formatDistanceToNow } from "@/lib/date";
import type { Document } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { FileIcon } from "./file-icon";
import { StatusBadge } from "./status-badge";
import { HealthBadge } from "./health-badge";
import { DeleteDocumentDialog } from "./delete-document-dialog";
import { ProcessingIndicator } from "./processing-indicator";
import { TagDialog } from "./tag-dialog";
import { UploadVersionButton } from "./upload-version-button";
import { useCurrentUser } from "@/hooks/use-user";

interface DocumentTableProps {
  documents: Document[];
  selectedIds: Set<string>;
  onSelectionChange: (next: Set<string>) => void;
  onDelete: (id: string) => Promise<void>;
  onRetry: (id: string) => Promise<void>;
  onUpdateTags: (id: string, tags: string[]) => Promise<void>;
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

function formatSize(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function DocumentTable({
  documents,
  selectedIds,
  onSelectionChange,
  onDelete,
  onRetry,
  onUpdateTags,
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
    <div className="overflow-hidden rounded-lg border border-border bg-background">
      <table className="w-full text-sm">
        <thead className="border-b border-border bg-muted/40 text-xs uppercase tracking-wider text-muted-foreground">
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
            <th className="px-4 py-2.5 text-left font-medium">Name</th>
            <th className="hidden px-4 py-2.5 text-left font-medium md:table-cell">
              Tags
            </th>
            <th className="hidden px-4 py-2.5 text-left font-medium md:table-cell">
              Size
            </th>
            <th className="hidden px-4 py-2.5 text-left font-medium lg:table-cell">
              Uploaded
            </th>
            <th className="px-4 py-2.5 text-left font-medium">Status</th>
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
}: {
  doc: Document;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  onDelete: (id: string) => Promise<void>;
  onRetry: (id: string) => Promise<void>;
  onUpdateTags: (id: string, tags: string[]) => Promise<void>;
}) {
  const [tagOpen, setTagOpen] = useState(false);
  const [savingTags, setSavingTags] = useState(false);
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";

  return (
    <tr
      className={
        selected
          ? "bg-primary/5 transition-colors"
          : "transition-colors hover:bg-muted/40"
      }
    >
      <td className="w-8 px-3 py-3">
        <Checkbox
          checked={selected}
          onCheckedChange={onSelect}
          aria-label={`Select ${doc.name}`}
        />
      </td>
      <td className="px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <FileIcon
            type={doc.file_type}
            className="h-4 w-4 shrink-0 text-muted-foreground"
          />
          <div className="min-w-0">
            <p className="truncate font-medium text-foreground">{doc.name}</p>
            <p className="truncate text-xs text-muted-foreground md:hidden">
              {doc.file_type.toUpperCase()} · {formatSize(doc.file_size_bytes)}
            </p>
          </div>
        </div>
      </td>
      <td className="hidden px-4 py-3 md:table-cell">
        <button
          type="button"
          onClick={() => setTagOpen(true)}
          className="group inline-flex max-w-44 flex-wrap items-center gap-1 rounded text-left"
          aria-label={`Edit tags for ${doc.name}`}
        >
          {(doc.tags ?? []).length === 0 ? (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground group-hover:text-foreground">
              <TagIcon className="h-3 w-3" />
              Add tags
            </span>
          ) : (
            <>
              {(doc.tags ?? []).slice(0, 3).map((t) => (
                <span
                  key={t}
                  className="rounded-full bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                >
                  {t}
                </span>
              ))}
              {(doc.tags ?? []).length > 3 && (
                <span className="text-[11px] text-muted-foreground">
                  +{(doc.tags ?? []).length - 3}
                </span>
              )}
              <Pencil className="ml-1 h-3 w-3 text-muted-foreground/40 group-hover:text-muted-foreground" />
            </>
          )}
        </button>
      </td>
      <td className="hidden px-4 py-3 text-muted-foreground md:table-cell">
        {formatSize(doc.file_size_bytes)}
      </td>
      <td className="hidden px-4 py-3 text-muted-foreground lg:table-cell">
        {formatDistanceToNow(doc.created_at)}
      </td>
      <td className="px-4 py-3">
        {doc.status === "processing" ? (
          <ProcessingIndicator startedAt={doc.created_at} />
        ) : (
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge
              status={doc.status}
              embeddingStats={extractEmbeddingStats(doc.metadata)}
            />
            {doc.status === "ready" && doc.health_label ? (
              <HealthBadge
                label={doc.health_label}
                score={doc.health_score ?? null}
              />
            ) : null}
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
          {isAdmin && doc.status === "ready" && (
            <UploadVersionButton
              documentId={doc.id}
              documentName={doc.name}
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
        title={`Tags for "${doc.name}"`}
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
