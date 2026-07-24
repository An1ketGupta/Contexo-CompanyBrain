"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronLeft, FolderPlus, Loader2, Pencil, Trash2 } from "lucide-react";
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
import { CollectionFormDialog } from "@/components/settings/collection-form-dialog";
import { useCollections, type Collection } from "@/hooks/use-collections";
import { useCurrentUser } from "@/hooks/use-user";

export default function CollectionsSettingsPage() {
  const { user, loading: userLoading } = useCurrentUser();
  const { collections, loading, error, remove } = useCollections();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Collection | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Collection | null>(null);
  const [deleting, setDeleting] = useState(false);

  const isAdmin = user?.role === "admin";

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (c: Collection) => {
    setEditing(c);
    setDialogOpen(true);
  };
  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await remove(deleteTarget.id);
      toast.success(`Deleted "${deleteTarget.name}".`);
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <div>
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-3 w-3" />
          Settings
        </Link>
        <div className="mt-2 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight">Collections</h1>
            <p className="mt-1 text-[15px] text-muted-foreground leading-relaxed max-w-[64ch]">
              Group documents by tag and pin chats to a collection for focused
              answers.
            </p>
          </div>
          {isAdmin && (
            <Button size="sm" onClick={openCreate}>
              <FolderPlus className="h-3.5 w-3.5" />
              New collection
            </Button>
          )}
        </div>
      </div>

      {!isAdmin && !userLoading && (
        <div className="rounded-xl border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          Only admins can create, edit, or delete collections.
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading collections…
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-destructive/20 bg-destructive-soft px-3 py-2 text-sm text-destructive-ink">
          {error}
        </div>
      ) : collections.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-muted/40 px-6 py-12 text-center">
          <p className="text-sm font-medium text-foreground">No collections yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Bundle related documents by tag, then pin a chat to that collection
            for focused answers.
          </p>
          {isAdmin && (
            <Button onClick={openCreate} size="sm" className="mt-4">
              <FolderPlus className="h-3.5 w-3.5" />
              Create your first collection
            </Button>
          )}
        </div>
      ) : (
        <ul className="space-y-2">
          {collections.map((col) => (
            <li
              key={col.id}
              className="flex items-center gap-3 rounded-2xl border border-border bg-card px-3 py-2.5"
            >
              <div
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-semibold text-white"
                style={{ backgroundColor: col.color }}
              >
                {col.icon || col.name.slice(0, 1).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">
                  {col.name}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {col.tag_filters.join(" · ") || "No tags"} ·{" "}
                  {col.document_count} document{col.document_count === 1 ? "" : "s"}
                </p>
                {col.description && (
                  <p className="truncate text-[11px] text-muted-foreground/80">
                    {col.description}
                  </p>
                )}
              </div>
              {isAdmin && (
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openEdit(col)}
                    aria-label={`Edit ${col.name}`}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setDeleteTarget(col)}
                    aria-label={`Delete ${col.name}`}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <CollectionFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        collection={editing}
      />

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete &quot;{deleteTarget?.name}&quot;?
            </AlertDialogTitle>
            <AlertDialogDescription>
              The collection will be removed but its tagged documents and any
              conversations pinned to it will keep working.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} disabled={deleting}>
              {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
