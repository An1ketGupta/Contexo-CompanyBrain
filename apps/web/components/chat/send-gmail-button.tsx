"use client";

/**
 * Gmail send action — surfaced inline in the message action row when:
 *   - the assistant turn looks like an email draft (intent === "task_generation"
 *     AND the body either has a Subject: line or matches an email-shaped
 *     opening like "Hi <name>,"), AND
 *   - the message has been persisted server-side (has server_id).
 *
 * Behavior:
 *   - First hover: trigger a lazy /api/integrations/gmail/status fetch so the
 *     button knows whether to show "Send via Gmail", "Connect Gmail" or the
 *     re-auth banner. Lazy so 100 messages in a thread aren't 100 fetches.
 *   - Click flow:
 *       not connected      → redirect to /settings/integrations
 *       missing send scope → inline banner with reconnect link
 *       connected + scope  → open SendGmailDialog
 *   - After a successful send: local "sent" badge replaces the button for the
 *     rest of the session. (Persistence across reloads is a follow-up day.)
 */

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Loader2, Mail, Send } from "lucide-react";
import type { CompetitorMatch } from "@/lib/types";
import { CompetitorConfirmDialog } from "./competitor-confirm-dialog";
import { SendGmailDialog } from "./send-gmail-dialog";
import { cn } from "@/lib/utils";

interface GmailStatus {
  connected: boolean;
  has_send_scope: boolean;
  email_address: string | null;
}

interface SendGmailButtonProps {
  messageId: string;
  body: string;
  competitorMatches?: CompetitorMatch[];
}

export function SendGmailButton({
  messageId,
  body,
  competitorMatches,
}: SendGmailButtonProps) {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [showReauthBanner, setShowReauthBanner] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    if (status || loadingStatus) return;
    setLoadingStatus(true);
    try {
      const res = await fetch("/api/integrations/gmail/status", { cache: "no-store" });
      if (res.ok) {
        const body = (await res.json()) as GmailStatus;
        setStatus(body);
      }
    } catch {
      // Swallow — user will see the error on click instead.
    } finally {
      setLoadingStatus(false);
    }
  }, [status, loadingStatus]);

  // Prefetch status when the message becomes visible to the user so the click
  // path doesn't show a spinner. IntersectionObserver would be tidier, but
  // the action row only renders on hover/last-message anyway, so onMount of
  // the button is already gated.
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  if (sentTo) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-success-tint px-2 py-1 text-[11px] font-medium text-success">
        <Send className="h-3 w-3" />
        Sent to {sentTo}
      </span>
    );
  }

  const handleClick = () => {
    if (!status) {
      // Status still loading — re-fetch and bail; user can click again.
      fetchStatus();
      return;
    }
    if (!status.connected) {
      window.location.href = "/settings/integrations?focus=gmail";
      return;
    }
    if (!status.has_send_scope) {
      setShowReauthBanner(true);
      return;
    }
    if (competitorMatches && competitorMatches.length > 0) {
      setConfirmOpen(true);
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
          status?.connected && status.has_send_scope
            ? `Send via Gmail (${status.email_address})`
            : "Send via Gmail"
        }
        aria-label="Send via Gmail"
      >
        {loadingStatus ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Mail className="h-3.5 w-3.5" />
        )}
      </button>

      {showReauthBanner && (
        <div className="ml-1 inline-flex items-center gap-1.5 rounded-md border border-amber/20 bg-amber-tint px-2 py-1 text-[11px] text-amber">
          <AlertCircle className="h-3 w-3" />
          <span>Reconnect Gmail to enable send.</span>
          <a
            href="/api/integrations/gmail/connect"
            className="font-medium underline underline-offset-2"
            onClick={async (e) => {
              e.preventDefault();
              const res = await fetch("/api/integrations/gmail/connect");
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

      {confirmOpen && competitorMatches && (
        <CompetitorConfirmDialog
          matches={competitorMatches}
          destination="Gmail"
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => {
            setConfirmOpen(false);
            setDialogOpen(true);
          }}
        />
      )}

      {dialogOpen && (
        <SendGmailDialog
          messageId={messageId}
          defaultBody={body}
          senderEmail={status?.email_address ?? null}
          onClose={() => setDialogOpen(false)}
          onSent={(to) => {
            setDialogOpen(false);
            setSentTo(to);
          }}
        />
      )}
    </>
  );
}
