"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { useConversations, type ConversationSummary } from "@/hooks/use-conversations";
import { useDebounced } from "@/hooks/use-debounced";
import { reportApiError, type ApiError } from "@/lib/errors";
import { Input } from "@/components/ui/input";
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
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { RenameDialog } from "./rename-dialog";

interface ConversationSidebarProps {
  activeId: string | null;
}

export function ConversationSidebar({ activeId }: ConversationSidebarProps) {
  const router = useRouter();
  const [searchInput, setSearchInput] = useState("");
  // Debounce so we don't hit the FTS RPC on every keystroke.
  const debouncedSearch = useDebounced(searchInput, 300);
  const searching = debouncedSearch.trim().length > 0;

  const { conversations, loading, error, rename, remove, setPinned } =
    useConversations(debouncedSearch);

  const pinnedConvos = conversations.filter((c) => c.is_pinned);
  const otherConvos = conversations.filter((c) => !c.is_pinned);

  const togglePin = async (c: ConversationSummary) => {
    try {
      await setPinned(c.id, !c.is_pinned);
    } catch (err) {
      reportApiError(err as ApiError);
    }
  };

  const [renaming, setRenaming] = useState<ConversationSummary | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ConversationSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const doRename = async (title: string) => {
    if (!renaming) return;
    try {
      await rename(renaming.id, title);
      toast.success("Conversation renamed.");
    } catch (err) {
      reportApiError(err as ApiError);
    }
  };

  const doDelete = async () => {
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await remove(confirmDelete.id);
      toast.success("Conversation deleted.");
      if (confirmDelete.id === activeId) router.push("/chat");
      setConfirmDelete(null);
    } catch (err) {
      reportApiError(err as ApiError);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <aside className="hidden h-full w-64 shrink-0 flex-col border-r border-border bg-background/60 md:flex">
      <div className="p-3">
        <Link
          href="/chat"
          className="flex w-full items-center justify-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-muted"
        >
          <MessageSquarePlus className="h-4 w-4" />
          New conversation
        </Link>
      </div>

      <div className="px-3 pb-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search conversations…"
            className="h-8 pl-8 pr-7 text-xs"
            aria-label="Search conversations"
          />
          {searchInput && (
            <button
              type="button"
              onClick={() => setSearchInput("")}
              aria-label="Clear search"
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {loading ? (
          <div className="space-y-1.5 px-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-7 bg-muted/60" />
            ))}
          </div>
        ) : error ? (
          <div className="px-2 text-xs text-destructive">{error}</div>
        ) : conversations.length === 0 ? (
          <div className="px-2 text-xs text-muted-foreground">
            {searching
              ? `No matches for "${debouncedSearch}".`
              : "No conversations yet."}
          </div>
        ) : searching ? (
          <>
            <SectionLabel>Results</SectionLabel>
            <ul className="space-y-0.5">
              {conversations.map((c) => (
                <ConversationRow
                  key={c.id}
                  convo={c}
                  active={c.id === activeId}
                  onRename={() => setRenaming(c)}
                  onDelete={() => setConfirmDelete(c)}
                  onTogglePin={() => togglePin(c)}
                />
              ))}
            </ul>
          </>
        ) : (
          <>
            {pinnedConvos.length > 0 && (
              <>
                <SectionLabel>Pinned</SectionLabel>
                <ul className="space-y-0.5">
                  {pinnedConvos.map((c) => (
                    <ConversationRow
                      key={c.id}
                      convo={c}
                      active={c.id === activeId}
                      onRename={() => setRenaming(c)}
                      onDelete={() => setConfirmDelete(c)}
                      onTogglePin={() => togglePin(c)}
                    />
                  ))}
                </ul>
                {otherConvos.length > 0 && (
                  <div className="my-1.5 h-px bg-border/60" />
                )}
              </>
            )}
            {otherConvos.length > 0 && (
              <>
                <SectionLabel>Recent</SectionLabel>
                <ul className="space-y-0.5">
                  {otherConvos.map((c) => (
                    <ConversationRow
                      key={c.id}
                      convo={c}
                      active={c.id === activeId}
                      onRename={() => setRenaming(c)}
                      onDelete={() => setConfirmDelete(c)}
                      onTogglePin={() => togglePin(c)}
                    />
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>

      {renaming && (
        <RenameDialog
          open={!!renaming}
          initialTitle={renaming.title ?? ""}
          onOpenChange={(o) => !o && setRenaming(null)}
          onConfirm={doRename}
        />
      )}

      <AlertDialog
        open={!!confirmDelete}
        onOpenChange={(o) => !o && !deleting && setConfirmDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmDelete?.title
                ? `"${confirmDelete.title}" and all its messages will be permanently deleted.`
                : "All messages in this conversation will be permanently deleted."}{" "}
              This can't be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                doDelete();
              }}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </aside>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

interface ConversationRowProps {
  convo: ConversationSummary;
  active: boolean;
  onRename: () => void;
  onDelete: () => void;
  onTogglePin: () => void;
}

function ConversationRow({
  convo,
  active,
  onRename,
  onDelete,
  onTogglePin,
}: ConversationRowProps) {
  const title = (convo.title ?? "").trim() || "Untitled";
  const isPinned = !!convo.is_pinned;

  return (
    <li className="group relative">
      <Link
        href={`/chat/${convo.id}`}
        className={cn(
          "flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors",
          active
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        {isPinned && (
          <Pin
            className="h-3 w-3 shrink-0 fill-current text-primary"
            aria-label="Pinned"
          />
        )}
        <span className="line-clamp-1 flex-1 pr-6">{title}</span>
      </Link>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              "absolute right-1 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground transition-opacity",
              active
                ? "opacity-100 hover:bg-background"
                : "opacity-0 hover:bg-background group-hover:opacity-100",
            )}
            aria-label="Conversation actions"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem onClick={onTogglePin}>
            {isPinned ? (
              <>
                <PinOff className="h-3.5 w-3.5" />
                Unpin
              </>
            ) : (
              <>
                <Pin className="h-3.5 w-3.5" />
                Pin
              </>
            )}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={onRename}>
            <Pencil className="h-3.5 w-3.5" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={onDelete}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}
