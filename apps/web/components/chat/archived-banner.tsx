"use client";

import { useState } from "react";
import { Archive, ArchiveRestore } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  useArchivedCount,
  useConversations,
} from "@/hooks/use-conversations";
import { reportApiError, type ApiError } from "@/lib/errors";
import { formatDistanceToNow } from "@/lib/date";

interface ArchivedBannerProps {
  conversationId: string;
  archivedAt: string | null | undefined;
  archiveReason: string | null | undefined;
  // Called after a successful restore so the chat page can refresh its
  // SWR cache without us having to thread mutate() through every layer.
  onRestored?: () => void;
}

/**
 * Banner shown at the top of an archived conversation. Surfaces:
 *   • when it was archived
 *   • whether the system did it ("auto-inactive") vs a human did
 *   • a one-click Restore button
 *
 * The chat input stays enabled below — V3 #104's UX decision was
 * auto-restore-on-message. The banner is a convenience for users who
 * want to flip the state without typing a new message.
 */
export function ArchivedBanner({
  conversationId,
  archivedAt,
  archiveReason,
  onRestored,
}: ArchivedBannerProps) {
  const { restore } = useConversations();
  const { refresh: refreshCount } = useArchivedCount();
  const [restoring, setRestoring] = useState(false);

  const rel = archivedAt
    ? formatDistanceToNow(new Date(archivedAt))
    : "recently";
  const auto = archiveReason === "auto_inactive";

  const handleRestore = async () => {
    setRestoring(true);
    try {
      await restore(conversationId);
      toast.success("Conversation restored.");
      refreshCount();
      onRestored?.();
    } catch (err) {
      reportApiError(err as ApiError);
    } finally {
      setRestoring(false);
    }
  };

  return (
    <div className="border-b border-amber/20 bg-amber-tint px-4 py-2.5">
      <div className="mx-auto flex max-w-3xl items-center gap-3">
        <Archive
          className="h-4 w-4 shrink-0 text-amber"
          aria-hidden
        />
        <p className="flex-1 text-sm text-amber">
          <span className="font-medium">Archived {rel}.</span>{" "}
          <span className="text-amber/80">
            {auto
              ? "Auto-archived for inactivity. Sending a message will restore it."
              : "Sending a message will restore it automatically."}
          </span>
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={handleRestore}
          disabled={restoring}
          className="shrink-0 border-amber/40 bg-background/60 hover:bg-background"
        >
          <ArchiveRestore className="h-3.5 w-3.5" />
          {restoring ? "Restoring…" : "Restore"}
        </Button>
      </div>
    </div>
  );
}
