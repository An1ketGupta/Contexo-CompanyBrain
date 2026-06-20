"use client";

/**
 * "Create in Notion" action — opens a dialog with a parent-page picker.
 * Renders only when the org has Notion connected (lazy-fetched). Shares the
 * /api/integrations/status lookup with the Slack button so opening a chat
 * page doesn't fire two parallel status calls.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ExternalLink,
  Loader2,
  Search,
  StickyNote,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface NotionStatus {
  available: boolean;
  connected: boolean;
}

interface NotionWriteTarget {
  id: string;
  title: string;
  url?: string;
}

interface ExportNotionButtonProps {
  messageId: string;
  body: string;
  // Heuristic-derived suggested title from the first line (passed in by caller).
  suggestedTitle: string;
}

export function ExportNotionButton({
  messageId,
  body,
  suggestedTitle,
}: ExportNotionButtonProps) {
  const [status, setStatus] = useState<NotionStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    if (status || loading) return;
    setLoading(true);
    try {
      const res = await fetch("/api/integrations/status", { cache: "no-store" });
      if (res.ok) {
        const payload = (await res.json()) as { notion?: NotionStatus };
        setStatus(payload.notion ?? { available: false, connected: false });
      }
    } catch {
      // Show on click.
    } finally {
      setLoading(false);
    }
  }, [status, loading]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  if (createdUrl) {
    return (
      <a
        href={createdUrl}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-700 hover:bg-emerald-500/15 dark:text-emerald-300"
      >
        <ExternalLink className="h-3 w-3" />
        Open in Notion
      </a>
    );
  }

  if (status && !status.available) return null;

  const handleClick = () => {
    if (!status) {
      fetchStatus();
      return;
    }
    if (!status.connected) {
      window.location.href = "/settings/integrations?focus=notion";
      return;
    }
    setDialogOpen(true);
  };

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        className={cn(
          "tap inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
        )}
        title="Create as Notion page"
        aria-label="Create as Notion page"
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <StickyNote className="h-3.5 w-3.5" />
        )}
      </button>
      {dialogOpen && (
        <ExportNotionDialog
          messageId={messageId}
          body={body}
          suggestedTitle={suggestedTitle}
          onClose={() => setDialogOpen(false)}
          onCreated={(url) => {
            setDialogOpen(false);
            setCreatedUrl(url);
          }}
        />
      )}
    </>
  );
}

function ExportNotionDialog({
  messageId,
  body,
  suggestedTitle,
  onClose,
  onCreated,
}: {
  messageId: string;
  body: string;
  suggestedTitle: string;
  onClose: () => void;
  onCreated: (url: string) => void;
}) {
  const [title, setTitle] = useState(suggestedTitle);
  const [content, setContent] = useState(body);
  const [pages, setPages] = useState<NotionWriteTarget[] | null>(null);
  const [pageQuery, setPageQuery] = useState("");
  const [selectedParent, setSelectedParent] = useState<NotionWriteTarget | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const loadPages = useCallback(async (query: string) => {
    setSearching(true);
    try {
      const q = query.trim();
      const url = q
        ? `/api/integrations/notion/write-targets?q=${encodeURIComponent(q)}`
        : "/api/integrations/notion/write-targets";
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) {
        setErrorMessage("Couldn't load Notion pages.");
        return;
      }
      const payload = (await res.json()) as { pages: NotionWriteTarget[] };
      setPages(payload.pages ?? []);
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    loadPages("");
  }, [loadPages]);

  const filteredPages = useMemo(() => pages ?? [], [pages]);

  const canCreate =
    !submitting && !!selectedParent && title.trim().length > 0 && content.trim().length > 0;

  const handleCreate = async () => {
    if (!canCreate || !selectedParent) return;
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await fetch("/api/integrations/notion/create-page", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: messageId,
          parent_page_id: selectedParent.id,
          parent_page_title: selectedParent.title,
          title: title.trim(),
          content,
        }),
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        const detail = payload.detail ?? "create_failed";
        if (detail === "notion_not_connected") {
          setErrorMessage("Notion isn't connected for this workspace.");
        } else if (detail === "message_already_sent") {
          setErrorMessage("This message has already been delivered.");
        } else {
          setErrorMessage("Couldn't queue the Notion page. Please try again.");
        }
        return;
      }
      // The Notion URL only exists after the Inngest worker runs. We close
      // the dialog optimistically — the action row will show "queued" until
      // delivery_status flips. (Day 7 / Agent Day 6 will wire realtime.)
      onCreated("about:blank#queued");
    } catch {
      setErrorMessage("Network error — please check your connection and retry.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Create Notion page</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="notion-title">Title</Label>
            <Input
              id="notion-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label>Parent page</Label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={pageQuery}
                onChange={(e) => setPageQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    loadPages(pageQuery);
                  }
                }}
                placeholder="Search shared pages…"
                className="pl-8"
              />
            </div>
            <div className="max-h-40 overflow-y-auto rounded-md border border-input">
              {searching ? (
                <div className="flex items-center justify-center py-6 text-xs text-muted-foreground">
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Searching…
                </div>
              ) : filteredPages.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  No pages found. Share a page with the Company Brain integration in Notion first.
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {filteredPages.slice(0, 30).map((p) => {
                    const selected = selectedParent?.id === p.id;
                    return (
                      <li key={p.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedParent(p)}
                          className={cn(
                            "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors",
                            selected ? "bg-primary/10 text-primary" : "hover:bg-muted",
                          )}
                        >
                          <StickyNote className="h-3 w-3 text-muted-foreground" />
                          <span className="flex-1 truncate font-medium">{p.title}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="notion-content">Content</Label>
            <Textarea
              id="notion-content"
              rows={8}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="text-xs"
            />
          </div>

          {errorMessage && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={!canCreate}>
            {submitting ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : null}
            Create page
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
