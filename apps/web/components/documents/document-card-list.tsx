"use client";

import { useState } from "react";
import {
  AlertCircle,
  MessageSquare,
  MoreVertical,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { formatAbsolute, formatRelativeShort } from "@/lib/date";
import type { Document } from "@/lib/types";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { toast } from "sonner";
import { FileIcon } from "./file-icon";
import { StatusBadge } from "./status-badge";

interface DocumentCardListProps {
  documents: Document[];
  onDelete: (id: string) => Promise<void>;
  onRetry: (id: string) => Promise<void>;
}

/**
 * Mobile-only card layout for /documents. Tag editing and bulk select are
 * intentionally desktop-only — they're cumbersome at thumb scale and the
 * mobile use case is "find and chat with the doc," not "tidy the library."
 */
export function DocumentCardList({
  documents,
  onDelete,
  onRetry,
}: DocumentCardListProps) {
  const [confirmDelete, setConfirmDelete] = useState<Document | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await onDelete(confirmDelete.id);
      toast.success(`Deleted "${confirmDelete.name}"`);
      setConfirmDelete(null);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to delete document.",
      );
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-2">
      {documents.map((doc) => (
        <DocumentCard
          key={doc.id}
          doc={doc}
          onDelete={() => setConfirmDelete(doc)}
          onRetry={() => onRetry(doc.id)}
        />
      ))}
      <AlertDialog
        open={!!confirmDelete}
        onOpenChange={(o) => !o && !deleting && setConfirmDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this document?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmDelete && (
                <>
                  <span className="font-medium text-foreground">
                    {confirmDelete.name}
                  </span>{" "}
                  and all of its chunks will be removed. The AI will no longer
                  have this context. This can&apos;t be undone.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleDelete();
              }}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function extractErrorReason(metadata: unknown): string | null {
  if (!metadata || typeof metadata !== "object") return null;
  const v = (metadata as Record<string, unknown>).error_reason;
  if (typeof v !== "string" || !v.trim()) return null;
  let msg = v
    .trim()
    .replace(/^Ingestion failed:\s*[A-Za-z_]+Error:\s*/i, "")
    .replace(/^Ingestion failed:\s*/i, "")
    .replace(/^Could not parse document:\s*/i, "");
  msg = msg.charAt(0).toUpperCase() + msg.slice(1);
  if (!/[.!?]$/.test(msg)) msg += ".";
  return msg;
}

function DocumentCard({
  doc,
  onDelete,
  onRetry,
}: {
  doc: Document;
  onDelete: () => void;
  onRetry: () => void;
}) {
  const tags = doc.tags ?? [];
  const errorReason = extractErrorReason(doc.metadata);
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-border bg-background p-3">
      <FileIcon type={doc.file_type} className="h-5 w-5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="line-clamp-1 text-sm font-medium text-foreground">
          {doc.name}
        </p>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
          {doc.current_version_number != null && (
            <>
              <span className="rounded-full bg-muted px-1.5 py-0.5 font-medium text-muted-foreground">
                v{doc.current_version_number}
              </span>
              <span>·</span>
            </>
          )}
          <StatusBadge status={doc.status} errorReason={errorReason} />
          <span>·</span>
          <span title={formatAbsolute(doc.created_at)}>
            {formatRelativeShort(doc.created_at)}
          </span>
        </div>
        {doc.status === "failed" && errorReason && (
          <div className="mt-1 flex items-start gap-1.5 text-[11px] leading-snug text-destructive/80">
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="line-clamp-2">{errorReason}</span>
          </div>
        )}
        {tags.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {tags.slice(0, 3).map((t) => (
              <span
                key={t}
                className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
              >
                {t}
              </span>
            ))}
            {tags.length > 3 && (
              <span className="text-[10px] text-muted-foreground">
                +{tags.length - 3}
              </span>
            )}
          </div>
        )}
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex h-11 w-11 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Document actions"
          >
            <MoreVertical className="h-4 w-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44">
          <DropdownMenuItem asChild>
            <a href={`/chat?document_id=${encodeURIComponent(doc.id)}`}>
              <MessageSquare className="h-3.5 w-3.5" />
              Chat with doc
            </a>
          </DropdownMenuItem>
          {doc.status === "failed" && (
            <DropdownMenuItem onClick={onRetry}>
              <RefreshCw className="h-3.5 w-3.5" />
              Retry processing
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            onClick={onDelete}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
