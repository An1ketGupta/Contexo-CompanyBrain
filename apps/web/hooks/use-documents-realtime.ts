"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { createClient } from "@/lib/supabase/client";
import type { Document, DocumentStatus } from "@/lib/types";
import { useCurrentUser } from "./use-user";

interface RealtimeHandlers {
  onUpsert: (doc: Document) => void;
  onRemove: (id: string) => void;
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
export function useDocumentsRealtime({ onUpsert, onRemove }: RealtimeHandlers) {
  const { organization } = useCurrentUser();
  const orgId = organization?.id ?? null;

  // Remember the last status we *toasted* about per doc, so reordering or
  // re-subscribing doesn't double-fire notifications.
  const lastStatusRef = useRef<Map<string, DocumentStatus>>(new Map());

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
              if (row.status === "ready" && prev !== "ready") {
                toast.success(`"${row.name}" is ready to use.`);
              } else if (row.status === "failed" && prev !== "failed") {
                toast.error(`"${row.name}" failed to process. Try re-uploading.`);
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
