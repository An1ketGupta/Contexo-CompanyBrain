"use client";

/**
 * "Create in Google Docs" action. Drive OAuth requires both drive.readonly
 * (existing) AND documents + drive.file (Day 4). If the org has Drive
 * connected but without the docs scope, we surface a re-auth banner —
 * mirrors the Gmail send-scope pattern from Day 2.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ExternalLink, FileText, Loader2 } from "lucide-react";
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

interface DriveStatus {
  available: boolean;
  connected: boolean;
  has_docs_write_scope: boolean;
}

interface ExportGDocsButtonProps {
  messageId: string;
  body: string;
  suggestedTitle: string;
}

export function ExportGDocsButton({
  messageId,
  body,
  suggestedTitle,
}: ExportGDocsButtonProps) {
  const [status, setStatus] = useState<DriveStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [showReauthBanner, setShowReauthBanner] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [createdQueued, setCreatedQueued] = useState(false);

  const fetchStatus = useCallback(async () => {
    if (status || loading) return;
    setLoading(true);
    try {
      const res = await fetch("/api/integrations/status", { cache: "no-store" });
      if (res.ok) {
        const payload = (await res.json()) as { drive?: DriveStatus };
        setStatus(
          payload.drive ?? {
            available: false,
            connected: false,
            has_docs_write_scope: false,
          },
        );
      }
    } catch {
      // surface on click
    } finally {
      setLoading(false);
    }
  }, [status, loading]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  if (createdQueued) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
        <ExternalLink className="h-3 w-3" />
        Doc queued
      </span>
    );
  }

  if (status && !status.available) return null;

  const handleClick = () => {
    if (!status) {
      fetchStatus();
      return;
    }
    if (!status.connected) {
      window.location.href = "/settings/integrations?focus=drive";
      return;
    }
    if (!status.has_docs_write_scope) {
      setShowReauthBanner(true);
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
          "inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
        )}
        title="Export as Google Doc"
        aria-label="Export as Google Doc"
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <FileText className="h-3.5 w-3.5" />
        )}
      </button>

      {showReauthBanner && (
        <div className="ml-1 inline-flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/60 dark:text-amber-300">
          <AlertCircle className="h-3 w-3" />
          <span>Reconnect Drive to enable Docs export.</span>
          <a
            href="/api/integrations/drive/connect"
            className="font-medium underline underline-offset-2"
            onClick={async (e) => {
              e.preventDefault();
              const res = await fetch("/api/integrations/drive/connect");
              if (res.ok) {
                const { auth_url } = (await res.json()) as { auth_url: string };
                window.location.href = auth_url;
              }
            }}
          >
            Reconnect
          </a>
        </div>
      )}

      {dialogOpen && (
        <ExportGDocsDialog
          messageId={messageId}
          body={body}
          suggestedTitle={suggestedTitle}
          onClose={() => setDialogOpen(false)}
          onQueued={() => {
            setDialogOpen(false);
            setCreatedQueued(true);
          }}
        />
      )}
    </>
  );
}

function ExportGDocsDialog({
  messageId,
  body,
  suggestedTitle,
  onClose,
  onQueued,
}: {
  messageId: string;
  body: string;
  suggestedTitle: string;
  onClose: () => void;
  onQueued: () => void;
}) {
  const [title, setTitle] = useState(suggestedTitle);
  const [content, setContent] = useState(body);
  const [shareWithMe, setShareWithMe] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const canCreate = !submitting && title.trim().length > 0 && content.trim().length > 0;

  const handleCreate = async () => {
    if (!canCreate) return;
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await fetch("/api/integrations/gdocs/create-doc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: messageId,
          title: title.trim(),
          content,
          share_with_me: shareWithMe,
        }),
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        const detail = payload.detail ?? "create_failed";
        if (detail === "docs_write_scope_missing") {
          setErrorMessage("Reconnect Drive from Settings → Integrations to enable Docs export.");
        } else if (detail === "drive_not_connected") {
          setErrorMessage("Drive isn't connected for this workspace.");
        } else if (detail === "message_already_sent") {
          setErrorMessage("This message has already been delivered.");
        } else {
          setErrorMessage("Couldn't queue the Google Doc. Please try again.");
        }
        return;
      }
      onQueued();
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
          <DialogTitle>Export to Google Docs</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="gdocs-title">Title</Label>
            <Input
              id="gdocs-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="gdocs-content">Content</Label>
            <Textarea
              id="gdocs-content"
              rows={10}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="text-xs"
            />
          </div>

          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={shareWithMe}
              onChange={(e) => setShareWithMe(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            <span>Share the doc back to me as writer</span>
          </label>

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
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
