"use client";

import { useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import {
  Archive,
  BarChart3,
  CalendarDays,
  FileText,
  HeartPulse,
  HelpCircle,
  Keyboard,
  LayoutTemplate,
  LineChart,
  MessageSquare,
  PieChart,
  Plus,
  Search,
  Settings,
  Shield,
} from "lucide-react";
import useSWR from "swr";
import { useCommandPalette } from "./command-palette-context";
import { useCurrentUser } from "@/hooks/use-user";
import { useShortcutsPanel } from "@/components/ui/shortcuts-panel-context";
import { networkError, parseApiError } from "@/lib/errors";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/hooks/use-conversations";
import type { Document } from "@/lib/types";

const fetcher = async (url: string) => {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    throw networkError(err);
  }
  if (!res.ok) throw await parseApiError(res);
  return res.json();
};

interface NavAction {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  href: string;
  keywords?: string[];
  adminOnly?: boolean;
  shortcut?: string;
}

const NAV_ACTIONS: NavAction[] = [
  {
    label: "New conversation",
    icon: Plus,
    href: "/chat",
    keywords: ["new", "start", "chat", "compose"],
    shortcut: "⌘N",
  },
  {
    label: "Chat",
    icon: MessageSquare,
    href: "/chat",
    keywords: ["conversations", "ask", "messages"],
  },
  {
    label: "Meeting prep",
    icon: CalendarDays,
    href: "/chat/meeting-prep",
    keywords: ["meeting", "prep", "brief", "agenda"],
  },
  {
    label: "Documents",
    icon: FileText,
    href: "/documents",
    keywords: ["files", "library", "uploads"],
  },
  {
    label: "Templates",
    icon: LayoutTemplate,
    href: "/settings/templates",
    keywords: ["prompts", "snippets", "presets"],
  },
  {
    label: "Insights",
    icon: LineChart,
    href: "/insights",
    keywords: ["activity", "history", "usage"],
    adminOnly: true,
  },
  {
    label: "Analytics",
    icon: BarChart3,
    href: "/admin/analytics",
    keywords: ["stats", "usage", "dashboard"],
    adminOnly: true,
  },
  {
    label: "Knowledge health",
    icon: HeartPulse,
    href: "/admin/health",
    keywords: ["health", "stale", "documents"],
    adminOnly: true,
  },
  {
    label: "Coverage",
    icon: PieChart,
    href: "/admin/coverage",
    keywords: ["gaps", "coverage", "topics"],
    adminOnly: true,
  },
  {
    label: "Moderation",
    icon: Shield,
    href: "/admin/moderation",
    keywords: ["flags", "review", "moderation"],
    adminOnly: true,
  },
  {
    label: "Archived conversations",
    icon: Archive,
    href: "/archive",
    keywords: ["archive", "archived", "old", "hidden", "restore"],
  },
  {
    label: "Settings",
    icon: Settings,
    href: "/settings",
    keywords: ["preferences", "account", "team"],
  },
  {
    label: "Help",
    icon: HelpCircle,
    href: "/help",
    keywords: ["support", "docs", "faq"],
  },
];

interface ConversationsResp { conversations: ConversationSummary[] }
interface DocumentsResp { documents: Document[] }

export function CommandPalette() {
  const router = useRouter();
  const { open, setOpen } = useCommandPalette();
  const { user } = useCurrentUser();
  const { setOpen: setShortcutsOpen } = useShortcutsPanel();
  const isAdmin = user?.role === "admin";

  // Only fetch dynamic lists once the palette has been opened at least once —
  // a fresh signup never paid for the request otherwise. SWR caches per key so
  // re-opening is instant.
  const { data: conversationsData } = useSWR<ConversationsResp>(
    open ? "/api/chat/conversations" : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 30_000 },
  );
  const { data: documentsData } = useSWR<DocumentsResp>(
    open ? "/api/documents?limit=50&sort_by=created_at&sort_dir=desc" : null,
    fetcher,
    { revalidateOnFocus: false, dedupingInterval: 60_000 },
  );

  const navItems = useMemo(
    () => NAV_ACTIONS.filter((a) => !a.adminOnly || isAdmin),
    [isAdmin],
  );

  // We re-render on every search keystroke; recent conversations + documents
  // are stable inputs so cmdk handles the fuzzy filter internally.
  const conversations = useMemo(() => {
    const list = conversationsData?.conversations ?? [];
    return list.slice(0, 12);
  }, [conversationsData]);

  const documents = useMemo(() => {
    const list = documentsData?.documents ?? [];
    return list.filter((d) => d.status === "ready").slice(0, 20);
  }, [documentsData]);

  const close = useCallback(() => setOpen(false), [setOpen]);
  const go = useCallback(
    (href: string) => {
      close();
      router.push(href);
    },
    [close, router],
  );

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className="fixed inset-0 z-[80] bg-black/40 backdrop-blur-sm
            data-[state=open]:animate-in data-[state=open]:fade-in-0
            data-[state=closed]:animate-out data-[state=closed]:fade-out-0"
        />
        <DialogPrimitive.Content
          // Pull focus to the search input and trap it inside the dialog. cmdk
          // wires arrow-keys / Enter on its own; we just stop Radix from
          // stealing them with default dialog auto-focus weirdness.
          className="fixed left-1/2 top-[18vh] z-[81] w-full max-w-lg -translate-x-1/2 px-4
            data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95
            data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95"
          onOpenAutoFocus={(e) => {
            // Let cmdk's input get focus naturally.
            e.preventDefault();
          }}
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            Jump to a page, conversation, or document.
          </DialogPrimitive.Description>
          <Command
            label="Command palette"
            className="overflow-hidden rounded-2xl border border-border bg-background shadow-2xl"
          >
            <div className="flex items-center gap-2 border-b border-border px-3.5 py-3">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
              <Command.Input
                autoFocus
                placeholder="Search conversations, documents, actions…"
                className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
              />
              <kbd className="hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
                Esc
              </kbd>
            </div>

            <Command.List className="max-h-[60vh] overflow-y-auto px-1.5 py-1.5">
              <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">
                No matches.
              </Command.Empty>

              <Command.Group
                heading="Navigation"
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground"
              >
                {navItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <Command.Item
                      key={item.label}
                      value={`${item.label} ${(item.keywords ?? []).join(" ")}`}
                      onSelect={() => go(item.href)}
                      className={itemClass}
                    >
                      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="flex-1 truncate">{item.label}</span>
                      {item.shortcut && (
                        <kbd className="ml-2 hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
                          {item.shortcut}
                        </kbd>
                      )}
                    </Command.Item>
                  );
                })}
                <Command.Item
                  value="keyboard shortcuts hotkeys help"
                  onSelect={() => {
                    close();
                    setShortcutsOpen(true);
                  }}
                  className={itemClass}
                >
                  <Keyboard className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate">Keyboard shortcuts</span>
                  <kbd className="ml-2 hidden shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
                    ?
                  </kbd>
                </Command.Item>
              </Command.Group>

              {conversations.length > 0 && (
                <Command.Group
                  heading="Recent conversations"
                  className="mt-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground"
                >
                  {conversations.map((c) => {
                    const title = c.title?.trim() || "Untitled conversation";
                    return (
                      <Command.Item
                        key={c.id}
                        value={`conv-${c.id} ${title}`}
                        onSelect={() => go(`/chat/${c.id}`)}
                        className={itemClass}
                      >
                        {c.is_pinned ? (
                          <MessageSquare className="h-4 w-4 shrink-0 text-amber-500" />
                        ) : (
                          <MessageSquare className="h-4 w-4 shrink-0 text-muted-foreground" />
                        )}
                        <span className="flex-1 truncate">{title}</span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              )}

              {documents.length > 0 && (
                <Command.Group
                  heading="Documents"
                  className="mt-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-foreground"
                >
                  {documents.map((doc) => (
                    <Command.Item
                      key={doc.id}
                      value={`doc-${doc.id} ${doc.name} ${(doc.tags ?? []).join(" ")}`}
                      onSelect={() => go(`/chat?document_id=${encodeURIComponent(doc.id)}`)}
                      className={itemClass}
                    >
                      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="flex-1 min-w-0">
                        <div className="truncate">{doc.name}</div>
                        <DocumentSubtitle doc={doc} />
                      </div>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}

              {documents.length === 0 && conversations.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted-foreground">
                  Tip: upload a document or start a chat — it&apos;ll show up here.
                </div>
              )}
            </Command.List>

            <div className="flex items-center justify-between gap-3 border-t border-border bg-muted/40 px-3 py-2 text-[10px] text-muted-foreground">
              <div className="flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-border bg-background px-1 font-mono">↑↓</kbd>
                  navigate
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-border bg-background px-1 font-mono">↵</kbd>
                  open
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-border bg-background px-1 font-mono">esc</kbd>
                  close
                </span>
              </div>
              <span className="hidden sm:inline">Nirnaya IQ</span>
            </div>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

const itemClass = cn(
  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-foreground",
  "aria-selected:bg-accent aria-selected:text-accent-foreground",
  "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
  "cursor-pointer outline-none",
);

function DocumentSubtitle({ doc }: { doc: Document }) {
  const summary = readSummary(doc.metadata);
  const tags = (doc.tags ?? []).slice(0, 3);
  const subtitle = summary
    ? truncate(summary, 80)
    : tags.length
      ? tags.join(" · ")
      : doc.file_type.toUpperCase();
  return (
    <div className="truncate text-[11px] text-muted-foreground">{subtitle}</div>
  );
}

function readSummary(metadata: unknown): string | null {
  if (!metadata || typeof metadata !== "object") return null;
  const v = (metadata as Record<string, unknown>).summary;
  return typeof v === "string" && v.trim() ? v : null;
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n).trimEnd()}…` : s;
}
