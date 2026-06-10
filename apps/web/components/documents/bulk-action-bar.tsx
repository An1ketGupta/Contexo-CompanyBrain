"use client";

import { useState } from "react";
import { Loader2, Tag, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
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
import { TagDialog } from "./tag-dialog";

interface Props {
  selectedIds: Set<string>;
  onClear: () => void;
  onBulkDelete: (
    ids: string[],
  ) => Promise<{ deleted: number; skipped: number }>;
  onBulkAddTags: (
    ids: string[],
    tags: string[],
  ) => Promise<{ updated: number; tags_applied: string[] }>;
}

export function BulkActionBar({
  selectedIds,
  onClear,
  onBulkDelete,
  onBulkAddTags,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [tagOpen, setTagOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  if (selectedIds.size === 0) return null;

  const ids = Array.from(selectedIds);
  const count = ids.length;
  const noun = count === 1 ? "document" : "documents";

  const doDelete = async () => {
    setBusy(true);
    try {
      const result = await onBulkDelete(ids);
      toast.success(
        result.skipped > 0
          ? `Deleted ${result.deleted}. ${result.skipped} skipped (no access).`
          : `Deleted ${result.deleted} ${result.deleted === 1 ? "document" : "documents"}.`,
      );
      onClear();
      setConfirmOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Bulk delete failed.");
    } finally {
      setBusy(false);
    }
  };

  const doTag = async (tags: string[]) => {
    setBusy(true);
    try {
      const result = await onBulkAddTags(ids, tags);
      toast.success(
        `Tagged ${result.updated} ${result.updated === 1 ? "document" : "documents"} with ${result.tags_applied.length} tag${result.tags_applied.length === 1 ? "" : "s"}.`,
      );
      setTagOpen(false);
      onClear();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Bulk tag failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div
        role="region"
        aria-label="Bulk actions"
        className="mb-2 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-sm"
      >
        <span className="font-medium text-foreground">
          {count} {noun} selected
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setTagOpen(true)}
            disabled={busy}
          >
            <Tag className="h-3.5 w-3.5" />
            Add tag
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setConfirmOpen(true)}
            disabled={busy}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </Button>
          <Button variant="ghost" size="sm" onClick={onClear} disabled={busy}>
            <X className="h-3.5 w-3.5" />
            Clear
          </Button>
        </div>
      </div>

      <AlertDialog
        open={confirmOpen}
        onOpenChange={(o) => !o && !busy && setConfirmOpen(false)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {count} {noun}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The {noun} and all chunks the AI extracted from them will be
              permanently removed from your knowledge base. This can&apos;t be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                doDelete();
              }}
              disabled={busy}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {busy && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
              Delete {count} {noun}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <TagDialog
        open={tagOpen}
        onOpenChange={setTagOpen}
        bulkCount={count}
        busy={busy}
        onSubmit={doTag}
      />
    </>
  );
}
