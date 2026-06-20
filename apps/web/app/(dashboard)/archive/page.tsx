"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Archive, ArchiveRestore, Loader2, Search, X } from "lucide-react";
import { toast } from "sonner";

import {
  useArchivedCount,
  useConversations,
  type ConversationSummary,
} from "@/hooks/use-conversations";
import { useDebounced } from "@/hooks/use-debounced";
import { reportApiError, type ApiError } from "@/lib/errors";
import { formatDistanceToNow } from "@/lib/date";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
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
import { cn } from "@/lib/utils";

export default function ArchivePage() {
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebounced(searchInput, 300);
  const searching = debouncedSearch.trim().length > 0;

  // The hook talks to the same endpoint as the sidebar but with
  // archived_only=true so the response is just the archive.
  const { conversations, loading, error, restore, bulkRestore, refresh } =
    useConversations(debouncedSearch, { archivedOnly: true });
  const { refresh: refreshCount } = useArchivedCount();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [bulkRestoring, setBulkRestoring] = useState(false);
  const [restoringId, setRestoringId] = useState<string | null>(null);

  const visibleIds = useMemo(
    () => conversations.map((c) => c.id),
    [conversations],
  );
  const allSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const someSelected = selected.size > 0;

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => {
      if (visibleIds.every((id) => prev.has(id))) {
        const next = new Set(prev);
        for (const id of visibleIds) next.delete(id);
        return next;
      }
      const next = new Set(prev);
      for (const id of visibleIds) next.add(id);
      return next;
    });
  };

  const doRestoreOne = async (c: ConversationSummary) => {
    if (restoringId) return;
    setRestoringId(c.id);
    try {
      await restore(c.id);
      toast.success("Restored.");
      refreshCount();
    } catch (err) {
      reportApiError(err as ApiError);
    } finally {
      setRestoringId(null);
    }
  };

  const doBulkRestore = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBulkRestoring(true);
    try {
      const result = await bulkRestore(ids);
      toast.success(
        `Restored ${result.restored_count} conversation${result.restored_count === 1 ? "" : "s"}.`,
      );
      setSelected(new Set());
      setConfirmBulk(false);
      refreshCount();
      refresh();
    } catch (err) {
      reportApiError(err as ApiError);
    } finally {
      setBulkRestoring(false);
    }
  };

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-4 p-4 md:p-8">
      <header className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
              <Archive className="h-5 w-5" />
              Archived conversations
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Conversations move here automatically after a period of inactivity, or
              when you archive them by hand. Pinned conversations are never
              auto-archived.
            </p>
          </div>
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search archived conversations…"
            className="pl-9 pr-9"
            aria-label="Search archived conversations"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => setSearchInput("")}
              aria-label="Clear search"
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </header>

      {/* Sticky bulk-action bar — only renders when something is picked. */}
      {someSelected && (
        <div className="sticky top-0 z-10 flex items-center justify-between gap-2 rounded-md border border-border bg-background p-2 shadow-sm">
          <div className="flex items-center gap-2 px-1 text-sm">
            <Checkbox
              checked={allSelected}
              onCheckedChange={toggleAll}
              aria-label="Select all"
            />
            <span className="text-muted-foreground">
              {selected.size} selected
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelected(new Set())}
            >
              Clear
            </Button>
            <Button size="sm" onClick={() => setConfirmBulk(true)}>
              <ArchiveRestore className="h-3.5 w-3.5" />
              Restore selected
            </Button>
          </div>
        </div>
      )}

      <main className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 bg-muted/60" />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
            {error}
          </div>
        ) : conversations.length === 0 ? (
          <EmptyState searching={searching} query={debouncedSearch} />
        ) : (
          <ul className="space-y-1.5">
            {conversations.map((c) => (
              <ArchiveRow
                key={c.id}
                convo={c}
                checked={selected.has(c.id)}
                restoring={restoringId === c.id}
                onToggle={() => toggleOne(c.id)}
                onRestore={() => doRestoreOne(c)}
              />
            ))}
          </ul>
        )}
      </main>

      <AlertDialog
        open={confirmBulk}
        onOpenChange={(o) => !o && !bulkRestoring && setConfirmBulk(false)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Restore {selected.size} conversation{selected.size === 1 ? "" : "s"}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              They'll reappear in your active conversation list. You can archive
              them again from the sidebar at any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={bulkRestoring}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                doBulkRestore();
              }}
              disabled={bulkRestoring}
            >
              {bulkRestoring ? "Restoring…" : "Restore"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ArchiveRow({
  convo,
  checked,
  restoring,
  onToggle,
  onRestore,
}: {
  convo: ConversationSummary;
  checked: boolean;
  restoring: boolean;
  onToggle: () => void;
  onRestore: () => void;
}) {
  const title = (convo.title ?? "").trim() || "Untitled";
  const archivedRel = convo.archived_at
    ? formatDistanceToNow(new Date(convo.archived_at))
    : null;
  const auto = convo.archive_reason === "auto_inactive";

  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded-md border border-border bg-card p-3 transition-colors",
        checked && "border-primary/50 bg-primary/5",
      )}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={onToggle}
        aria-label={`Select "${title}"`}
      />
      <Link href={`/chat/${convo.id}`} className="flex-1 min-w-0">
        <div className="text-sm font-medium text-foreground line-clamp-1">
          {title}
        </div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          {archivedRel ? `Archived ${archivedRel}` : "Archived"}
          {auto && " · auto"}
        </div>
      </Link>
      <Button
        variant="ghost"
        size="sm"
        onClick={onRestore}
        disabled={restoring}
        className="shrink-0"
      >
        {restoring ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <ArchiveRestore className="h-3.5 w-3.5" />
        )}
        {restoring ? "Restoring…" : "Restore"}
      </Button>
    </li>
  );
}

function EmptyState({
  searching,
  query,
}: {
  searching: boolean;
  query: string;
}) {
  if (searching) {
    return (
      <div className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
        No archived conversations match &ldquo;{query}&rdquo;.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-dashed border-border p-10 text-center">
      <Archive className="mx-auto h-8 w-8 text-muted-foreground/50" />
      <p className="mt-3 text-sm font-medium">No archived conversations yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Conversations you archive (or that go untouched past your team&rsquo;s
        threshold) will show up here.
      </p>
      <Link
        href="/chat"
        className="mt-4 inline-flex items-center gap-1 text-xs text-primary hover:underline"
      >
        Back to conversations →
      </Link>
    </div>
  );
}
