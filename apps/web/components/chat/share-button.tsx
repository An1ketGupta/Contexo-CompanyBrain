"use client";

import { useState } from "react";
import { Check, Link2, Loader2, Share2 } from "lucide-react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { parseApiError, type ApiError } from "@/lib/errors";

/**
 * V3 Day 4 #62 — Share button on assistant messages.
 *
 * Two-tap UX: first click mints (or fetches) the public URL and copies it
 * to the clipboard. A dropdown surfaces revoke + view count when the
 * message already has an active share.
 *
 * State lives in the button itself — we only hit the server on click. That
 * keeps message rendering cheap; the share endpoint can be called as many
 * times as needed since it's idempotent on the active token.
 */
export function ShareButton({ messageId }: { messageId: string }) {
  const [busy, setBusy] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [justCopied, setJustCopied] = useState(false);
  const [viewCount, setViewCount] = useState<number | null>(null);
  const [open, setOpen] = useState(false);

  const ensureShareState = async () => {
    try {
      const res = await fetch(`/api/chat/messages/${encodeURIComponent(messageId)}/share`);
      if (!res.ok) return;
      const data = await res.json();
      if (data?.is_shared) {
        setShareUrl(data.url ?? null);
        setViewCount(data.view_count ?? 0);
      } else {
        setShareUrl(null);
        setViewCount(null);
      }
    } catch {
      // Lazy lookup — silent on failure; clicking will re-create.
    }
  };

  const createShare = async () => {
    setBusy(true);
    try {
      const res = await fetch(
        `/api/chat/messages/${encodeURIComponent(messageId)}/share`,
        { method: "POST" },
      );
      if (!res.ok) throw await parseApiError(res);
      const data = await res.json();
      const url: string = data.url;
      setShareUrl(url);
      setViewCount(data.view_count ?? 0);
      await navigator.clipboard.writeText(url).catch(() => {
        /* clipboard blocked — link is still in the dropdown */
      });
      setJustCopied(true);
      setTimeout(() => setJustCopied(false), 1500);
      toast.success("Link copied to clipboard");
      setOpen(true);
    } catch (err) {
      const apiErr = err as ApiError;
      toast.error(apiErr.message ?? "Couldn't create share link.");
    } finally {
      setBusy(false);
    }
  };

  const revokeShare = async () => {
    setBusy(true);
    try {
      const res = await fetch(
        `/api/chat/messages/${encodeURIComponent(messageId)}/share`,
        { method: "DELETE" },
      );
      if (!res.ok && res.status !== 204) throw await parseApiError(res);
      setShareUrl(null);
      setViewCount(null);
      toast.success("Share link revoked");
      setOpen(false);
    } catch (err) {
      const apiErr = err as ApiError;
      toast.error(apiErr.message ?? "Couldn't revoke share link.");
    } finally {
      setBusy(false);
    }
  };

  const copyExisting = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setJustCopied(true);
      setTimeout(() => setJustCopied(false), 1500);
      toast.success("Link copied to clipboard");
    } catch {
      toast.error("Couldn't access the clipboard.");
    }
  };

  // If we have no state yet, the first click should always create (idempotent).
  // If we already know there's a share, open the dropdown for management.
  return (
    <DropdownMenu
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) void ensureShareState();
      }}
    >
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onClick={(e) => {
            // First click with no known state → mint + copy directly,
            // skipping the dropdown so it feels like a one-click share.
            if (!shareUrl) {
              e.preventDefault();
              void createShare();
            }
          }}
          disabled={busy}
          className="inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
          aria-label="Share this output"
          title="Share this output"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : justCopied ? (
            <Check className="h-3.5 w-3.5 text-emerald-600" />
          ) : (
            <Share2 className="h-3.5 w-3.5" />
          )}
        </button>
      </DropdownMenuTrigger>
      {shareUrl && (
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuLabel className="text-xs font-medium">
            Public link
            {viewCount !== null && (
              <span className="ml-1 font-normal text-muted-foreground">
                · {viewCount} {viewCount === 1 ? "view" : "views"}
              </span>
            )}
          </DropdownMenuLabel>
          <div className="px-2 pb-2">
            <code className="block truncate rounded bg-muted px-2 py-1 text-[11px] text-foreground/80">
              {shareUrl}
            </code>
          </div>
          <DropdownMenuItem onClick={copyExisting}>
            <Link2 className="h-3.5 w-3.5" />
            Copy link
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={revokeShare}
            className="text-destructive focus:text-destructive"
          >
            Revoke link
          </DropdownMenuItem>
        </DropdownMenuContent>
      )}
    </DropdownMenu>
  );
}
