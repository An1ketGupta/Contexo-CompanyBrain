"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface NotionPage {
  id: string;
  title: string;
  url: string | null;
  last_edited_time?: string | null;
}

interface NotionParentStatus {
  connected: boolean;
  parent_id: string | null;
  parent_title: string | null;
  accessible: boolean;
  accessibility_error: string | null;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

async function fetchAuthUrl(): Promise<string> {
  const res = await fetch("/api/integrations/notion/connect");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.message || `Failed (${res.status})`);
  }
  const data: { auth_url: string } = await res.json();
  return data.auth_url;
}

/**
 * Modal for picking the org's default Notion parent page (or overriding it
 * for a single requisition). Two modes:
 *
 *   - `scope="org"`     — saves the choice to the org-level default.
 *   - `scope="publish"` — returns the choice to the caller without saving;
 *                         used as a per-requisition override on the publish
 *                         form.
 */
export function NotionParentPicker({
  open,
  onOpenChange,
  scope,
  onPicked,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  scope: "org" | "publish";
  onPicked?: (page: NotionPage) => void;
}) {
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState(false);

  const { data: status, mutate: mutateStatus } =
    useSWR<NotionParentStatus>(
      open ? "/api/recruiting/notion-parent" : null,
      fetcher,
      { revalidateOnFocus: false },
    );

  const pagesKey = useMemo(() => {
    if (!open || !status?.connected) return null;
    return query
      ? `/api/integrations/notion/write-targets?q=${encodeURIComponent(query)}`
      : "/api/integrations/notion/write-targets";
  }, [open, status?.connected, query]);

  const {
    data: pagesResponse,
    isLoading: loadingPages,
    mutate: mutatePages,
  } = useSWR<{ pages: NotionPage[] }>(pagesKey, fetcher, {
    revalidateOnFocus: false,
  });

  const pages = pagesResponse?.pages ?? [];

  // Reset query when the modal opens so re-opens don't keep stale filter.
  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const authUrl = await fetchAuthUrl();
      // Send the user to Notion's consent screen; they'll return to the
      // settings redirect (default `_settings_redirect`). After that the
      // recruiting page can re-fetch and the picker shows real pages.
      window.location.href = authUrl;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start Notion connect.");
      setConnecting(false);
    }
  };

  const handlePick = async (page: NotionPage) => {
    if (scope === "publish") {
      onPicked?.(page);
      onOpenChange(false);
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/recruiting/notion-parent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          parent_page_id: page.id,
          parent_page_title: page.title,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.message || body?.detail || `Failed (${res.status})`);
      }
      toast.success(`Hiring trackers will nest under "${page.title}"`);
      await mutateStatus();
      // Surface the change to anyone else reading the same endpoint (the
      // empty-state card on /recruiting reads from this key).
      await globalMutate("/api/recruiting/notion-parent");
      onPicked?.(page);
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save parent.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {scope === "org"
              ? "Pick a parent page for hiring trackers"
              : "Use a different parent for this requisition"}
          </DialogTitle>
        </DialogHeader>

        {status && !status.connected ? (
          <div className="space-y-3 rounded-md border border-border bg-muted/40 p-4 text-sm">
            <p className="font-medium">Connect Notion to get started</p>
            <p className="text-muted-foreground">
              We&apos;ll redirect you to Notion to pick a workspace and grant
              access. When you come back, your shared pages will appear here.
            </p>
            <Button onClick={handleConnect} disabled={connecting}>
              {connecting ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Redirecting…
                </>
              ) : (
                "Connect Notion"
              )}
            </Button>
          </div>
        ) : (
          <>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search shared pages…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="pl-8"
                />
              </div>
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => mutatePages()}
                aria-label="Refresh page list"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </Button>
            </div>

            <div className="max-h-72 overflow-y-auto rounded border border-border">
              {loadingPages ? (
                <div className="flex items-center justify-center p-6 text-xs text-muted-foreground">
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Loading pages…
                </div>
              ) : pages.length === 0 ? (
                <div className="space-y-3 p-4 text-sm">
                  <div className="flex items-start gap-2 text-amber-700 dark:text-amber-300">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>
                      No pages shared with NirnayaIQ yet. Go to Notion to grant
                      access to the page you want as the parent.
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Two ways to do this:
                  </p>
                  <ol className="ml-4 list-decimal space-y-1.5 text-xs text-muted-foreground">
                    <li>
                      <strong>Re-authorise NirnayaIQ:</strong> opens the Notion
                      consent screen where you can pick which pages to share.
                    </li>
                    <li>
                      <strong>From a page in Notion:</strong> click the{" "}
                      <strong>···</strong> menu →{" "}
                      <strong>Connections → NirnayaIQ</strong>, then return
                      here and click refresh.
                    </li>
                  </ol>
                  <Button size="sm" onClick={handleConnect} disabled={connecting}>
                    {connecting ? (
                      <>
                        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        Redirecting…
                      </>
                    ) : (
                      "Re-authorise NirnayaIQ in Notion"
                    )}
                  </Button>
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {pages.map((p) => {
                    const isCurrent = status?.parent_id === p.id;
                    return (
                      <li key={p.id}>
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => handlePick(p)}
                          className="flex w-full items-start justify-between gap-3 p-3 text-left text-sm transition hover:bg-accent/50 disabled:opacity-50"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate font-medium">{p.title}</p>
                            {p.url && (
                              <p className="truncate text-xs text-muted-foreground">
                                {p.url}
                              </p>
                            )}
                          </div>
                          {isCurrent && (
                            <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              current
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <p className="text-xs text-muted-foreground">
              Don&apos;t see the page you want?{" "}
              <button
                type="button"
                onClick={handleConnect}
                disabled={connecting}
                className="text-blue-600 hover:underline disabled:opacity-60"
              >
                Share more pages via Notion
              </button>
            </p>
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
