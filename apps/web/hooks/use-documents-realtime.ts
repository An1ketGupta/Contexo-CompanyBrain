"use client";

import { useEffect } from "react";
import { createClient } from "@/lib/supabase/client";
import type { Document } from "@/lib/types";
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
 */
export function useDocumentsRealtime({ onUpsert, onRemove }: RealtimeHandlers) {
  const { organization } = useCurrentUser();
  const orgId = organization?.id ?? null;

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
            if (id) onRemove(id);
            return;
          }

          const row = payload.new as Document | null;
          if (row?.id) onUpsert(row);
        },
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [orgId, onUpsert, onRemove]);
}
