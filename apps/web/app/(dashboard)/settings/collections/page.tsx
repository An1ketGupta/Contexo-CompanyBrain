"use client";

import { useEffect, useState } from "react";
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
            <h1 className="text-xl font-semibold tracking-tight">Collections</h1>
            <p className="mt-1 text-sm text-muted-foreground">
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
        <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          Only admins can create, edit, or delete collections.
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading collections…
        </div>
      ) : error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : collections.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-muted/30 px-6 py-10 text-center">
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
              className="flex items-center gap-3 rounded-xl border border-border bg-background px-3 py-2.5"
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

      <RfpApprovedCollectionSection
        collections={collections}
        isAdmin={isAdmin}
      />

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


function RfpApprovedCollectionSection({
  collections,
  isAdmin,
}: {
  collections: Collection[];
  isAdmin: boolean;
}) {
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/organizations/me")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        setCurrentId(d?.rfp_approved_collection_id ?? null);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async (next: string | null) => {
    setSaving(true);
    try {
      const res = await fetch("/api/organizations/me/rfp-collection", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rfp_approved_collection_id: next }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error((detail as { detail?: string })?.detail || "Failed to save.");
      }
      setCurrentId(next);
      toast.success(
        next
          ? "RFP-approved collection saved."
          : "RFP-approved collection cleared. RFP agent will use the whole KB.",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3 rounded-xl border border-border bg-muted/20 p-4">
      <div>
        <h2 className="text-sm font-semibold">RFP-approved collection</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          The RFP Agent uses this collection as the default retrieval scope, so customer-facing
          answers don&apos;t accidentally pull from internal-only documents. Leave unset to allow
          the whole KB.
        </p>
      </div>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading…
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm"
            value={currentId ?? ""}
            disabled={!isAdmin || saving}
            onChange={(e) => save(e.target.value || null)}
          >
            <option value="">— Not set (use whole KB) —</option>
            {collections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.document_count} docs)
              </option>
            ))}
          </select>
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
      )}
      {!isAdmin && (
        <p className="text-[11px] text-muted-foreground">
          Only admins can change this setting.
        </p>
      )}
    </div>
  );
}
