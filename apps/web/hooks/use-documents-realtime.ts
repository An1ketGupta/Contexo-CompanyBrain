"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import type { Document, DocumentStatus } from "@/lib/types";
import { useCurrentUser } from "./use-user";

interface RealtimeHandlers {
  onUpsert: (doc: Document) => void;
  onRemove: (id: string) => void;
  /**
   * Suppress success/failure toasts. Useful when a separate global hook
   * (see `useDocumentReadyToast`) is already responsible for surfacing
   * notifications — otherwise the toast double-fires on the documents page.
   */
  silentToasts?: boolean;
}

interface PendingToast {
  name: string;
  kind: "ready" | "failed";
}

/**
 * Subscribe to `postgres_changes` on the documents table for the current org.
 * RLS guarantees we only get rows our session can SELECT, but we also filter
 * by org_id explicitly so the channel name is org-scoped and we don't even
 * receive cross-org WAL events on the wire.
 *
 * Notification rules:
 *   * pending/processing → ready  ⇒ success toast
 *   * any              → failed  ⇒ error toast (and we don't re-toast on each
 *                                   subsequent UPDATE while it stays failed)
 *   * Inserts and routine status transitions stay silent — the table itself
 *     is reactive enough.
 */
export function useDocumentsRealtime({
  onUpsert,
  onRemove,
  silentToasts,
}: RealtimeHandlers) {
  const { organization } = useCurrentUser();
  const orgId = organization?.id ?? null;

  // Remember the last status we *toasted* about per doc, so reordering or
  // re-subscribing doesn't double-fire notifications.
  const lastStatusRef = useRef<Map<string, DocumentStatus>>(new Map());

  // V4 #60: defer toasts that fire while the tab is hidden so the user gets
  // an actionable "welcome back" surface instead of a buried notification.
  const queuedRef = useRef<PendingToast[]>([]);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const drain = () => {
      if (document.hidden) return;
      const queue = queuedRef.current;
      if (queue.length === 0) return;
      queuedRef.current = [];
      for (const item of queue) {
        emitDocStatusToast(item);
      }
    };
    document.addEventListener("visibilitychange", drain);
    drain();
    return () => document.removeEventListener("visibilitychange", drain);
  }, []);

  useEffect(() => {
    if (!orgId) return;

    const supabase = createClient();
    const channel = supabase
      .channel(`documents:org:${orgId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "documents",
          filter: `org_id=eq.${orgId}`,
        },
        (payload) => {
          if (payload.eventType === "DELETE") {
            const id = (payload.old as { id?: string }).id;
            if (id) {
              onRemove(id);
              lastStatusRef.current.delete(id);
            }
            return;
          }

          const row = payload.new as Document | null;
          if (!row?.id) return;

          // Fire toasts on real status transitions only — never on INSERT
          // (the upload toast already covered that) and never on idempotent
          // UPDATEs that don't change status.
          if (payload.eventType === "UPDATE") {
            const prev = lastStatusRef.current.get(row.id);
            if (prev !== row.status) {
              const pending: PendingToast | null =
                row.status === "ready" && prev !== "ready"
                  ? { name: row.name, kind: "ready" }
                  : row.status === "failed" && prev !== "failed"
                    ? { name: row.name, kind: "failed" }
                    : null;
              if (pending && !silentToasts) {
                if (
                  typeof document !== "undefined" &&
                  document.hidden
                ) {
                  queuedRef.current.push(pending);
                } else {
                  emitDocStatusToast(pending);
                }
              }
              lastStatusRef.current.set(row.id, row.status);
            }
          } else {
            // INSERT — track current status without a toast.
            lastStatusRef.current.set(row.id, row.status);
          }

          onUpsert(row);
        },
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [orgId, onUpsert, onRemove]);
}

function emitDocStatusToast(pending: PendingToast) {
  if (pending.kind === "ready") {
    toast.success(`"${pending.name}" is ready to use.`, {
      description: "Ask a question and the AI will draw on it.",
      action: {
        label: "Ask",
        onClick: () => {
          if (typeof window !== "undefined") {
            window.location.href = "/chat";
          }
        },
      },
      duration: 8000,
    });
  } else {
    toast.error(`"${pending.name}" failed to process. Try re-uploading.`);
  }
}
