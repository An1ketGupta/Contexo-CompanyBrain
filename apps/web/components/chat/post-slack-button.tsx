"use client";

/**
 * Slack post action — sibling of SendGmailButton. Shows up in the message
 * action row on any assistant turn (no email-heuristic gating; Slack posts
 * are valid for any kind of output — announcements, summaries, decisions,
 * Q&A copy-paste).
 *
 * Lazy-fetches /api/integrations/status so the button knows whether Slack
 * is connected before the user clicks. On click:
 *   not connected → bounce to /settings/integrations
 *   connected     → open PostSlackDialog (channel picker + thread + send)
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, MessageSquare } from "lucide-react";
import { PostSlackDialog } from "./post-slack-dialog";
import { cn } from "@/lib/utils";

interface SlackStatus {
  available: boolean;
  connected: boolean;
  workspace_name: string | null;
}

interface PostSlackButtonProps {
  messageId: string;
  body: string;
}

export function PostSlackButton({ messageId, body }: PostSlackButtonProps) {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [postedTo, setPostedTo] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    if (status || loading) return;
    setLoading(true);
    try {
      // Reuse the existing /integrations/status endpoint — no need for a
      // dedicated /slack/status route. The shape is already wired and the
      // payload is small (one row per integration).
      const res = await fetch("/api/integrations/status", { cache: "no-store" });
      if (res.ok) {
        const body = (await res.json()) as { slack?: SlackStatus };
        setStatus(body.slack ?? { available: false, connected: false, workspace_name: null });
      }
    } catch {
      // Surface the error on click instead of silently failing here.
    } finally {
      setLoading(false);
    }
  }, [status, loading]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  if (postedTo) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
        <MessageSquare className="h-3 w-3" />
        Posted to #{postedTo}
      </span>
    );
  }

  if (status && !status.available) {
    // Slack OAuth isn't even configured on this deploy — don't render the
    // button at all. (Matches the Gmail "no client_id" pattern.)
    return null;
  }

  const handleClick = () => {
    if (!status) {
      fetchStatus();
      return;
    }
    if (!status.connected) {
      window.location.href = "/settings/integrations?focus=slack";
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
        title={
          status?.connected
            ? `Post to Slack${status.workspace_name ? ` (${status.workspace_name})` : ""}`
            : "Post to Slack"
        }
        aria-label="Post to Slack"
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <MessageSquare className="h-3.5 w-3.5" />
        )}
      </button>

      {dialogOpen && (
        <PostSlackDialog
          messageId={messageId}
          defaultBody={body}
          workspaceName={status?.workspace_name ?? null}
          onClose={() => setDialogOpen(false)}
          onPosted={(channelName) => {
            setDialogOpen(false);
            setPostedTo(channelName);
          }}
        />
      )}
    </>
  );
}
