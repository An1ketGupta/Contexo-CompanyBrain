"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  MessageSquare,
  MessageSquarePlus,
  Pin,
  PinOff,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
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
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useConversations,
  type ConversationSummary,
} from "@/hooks/use-conversations";
import { useDebounced } from "@/hooks/use-debounced";
import { reportApiError, type ApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";

interface MobileConversationDrawerProps {
  activeId: string | null;
}

/**
 * V3 Day 3 #27 — mobile-only drawer that mirrors ConversationSidebar.
 *
 * Triggered by a floating button at the top-left of the chat surface.
 * Shares the underlying useConversations hook with the desktop sidebar so
 * a pin/rename/delete made in either surface stays in sync.
 */
export function MobileConversationDrawer({ activeId }: MobileConversationDrawerProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const debouncedSearch = useDebounced(searchInput, 300);
  const searching = debouncedSearch.trim().length > 0;

  const { conversations, loading, error, remove, setPinned } =
    useConversations(debouncedSearch);

  const [confirmDelete, setConfirmDelete] =
    useState<ConversationSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const pinned = conversations.filter((c) => c.is_pinned);
  const other = conversations.filter((c) => !c.is_pinned);

  const togglePin = async (c: ConversationSummary) => {
    try {
      await setPinned(c.id, !c.is_pinned);
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
      setOpen(false);
    } catch (err) {
      reportApiError(err as ApiError);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <button
            type="button"
            className="flex h-11 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-muted md:hidden"
            aria-label="Open conversations"
          >
            <MessageSquare className="h-4 w-4" />
            Conversations
          </button>
        </SheetTrigger>
        <SheetContent side="left" className="flex w-72 flex-col p-0">
          <SheetTitle className="sr-only">Conversations</SheetTitle>

          <div className="flex items-center justify-between border-b border-border px-3 py-3">
            <span className="text-sm font-semibold tracking-tight">
              Conversations
            </span>
            <Link
              href="/chat"
              onClick={() => setOpen(false)}
              className="flex items-center gap-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              <MessageSquarePlus className="h-3.5 w-3.5" />
              New
            </Link>
          </div>

          <div className="border-b border-border px-3 py-2.5">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search conversations…"
                className="h-9 pl-8 pr-7 text-sm"
                aria-label="Search conversations"
              />
              {searchInput && (
                <button
                  type="button"
                  onClick={() => setSearchInput("")}
                  aria-label="Clear search"
                  className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-2 py-2">
            {loading ? (
              <div className="space-y-1.5 px-1">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-9 bg-muted/60" />
                ))}
              </div>
            ) : error ? (
              <div className="px-2 text-sm text-destructive">{error}</div>
            ) : conversations.length === 0 ? (
              <div className="px-2 py-4 text-sm text-muted-foreground">
                {searching
                  ? `No matches for "${debouncedSearch}".`
                  : "No conversations yet."}
              </div>
            ) : (
              <>
                {searching ? (
                  <SectionLabel>Results</SectionLabel>
                ) : (
                  pinned.length > 0 && <SectionLabel>Pinned</SectionLabel>
                )}
                <ul className="space-y-0.5">
                  {(searching ? conversations : pinned).map((c) => (
                    <MobileRow
                      key={c.id}
                      convo={c}
                      active={c.id === activeId}
                      onNavigate={() => setOpen(false)}
                      onTogglePin={() => togglePin(c)}
                      onDelete={() => setConfirmDelete(c)}
                    />
                  ))}
                </ul>
                {!searching && other.length > 0 && (
                  <>
                    {pinned.length > 0 && (
                      <div className="my-1.5 h-px bg-border/60" />
                    )}
                    <SectionLabel>Recent</SectionLabel>
                    <ul className="space-y-0.5">
                      {other.map((c) => (
                        <MobileRow
                          key={c.id}
                          convo={c}
                          active={c.id === activeId}
                          onNavigate={() => setOpen(false)}
                          onTogglePin={() => togglePin(c)}
                          onDelete={() => setConfirmDelete(c)}
                        />
                      ))}
                    </ul>
                  </>
                )}
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

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
    </>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

function MobileRow({
  convo,
  active,
  onNavigate,
  onTogglePin,
  onDelete,
}: {
  convo: ConversationSummary;
  active: boolean;
  onNavigate: () => void;
  onTogglePin: () => void;
  onDelete: () => void;
}) {
  const title = (convo.title ?? "").trim() || "Untitled";
  const isPinned = !!convo.is_pinned;

  return (
    <li className="flex items-center gap-1">
      <Link
        href={`/chat/${convo.id}`}
        onClick={onNavigate}
        className={cn(
          "flex min-h-[44px] flex-1 items-center gap-1.5 rounded-md px-2 py-2 text-sm transition-colors",
          active
            ? "bg-accent text-accent-foreground"
            : "text-foreground hover:bg-muted",
        )}
      >
        {isPinned && (
          <Pin className="h-3 w-3 shrink-0 fill-current text-primary" />
        )}
        <span className="line-clamp-1 flex-1">{title}</span>
      </Link>
      <button
        type="button"
        onClick={onTogglePin}
        className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
        aria-label={isPinned ? "Unpin conversation" : "Pin conversation"}
      >
        {isPinned ? (
          <PinOff className="h-4 w-4" />
        ) : (
          <Pin className="h-4 w-4" />
        )}
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
        aria-label="Delete conversation"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </li>
  );
}
