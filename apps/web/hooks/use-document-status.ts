"use client";

import { useEffect, useId } from "react";
import useSWR from "swr";
import { createClient } from "@/lib/supabase/client";
import type { DocumentStatusSummary } from "@/lib/types";
import { useCurrentUser } from "./use-user";

const KEY = "/api/organizations/document-status";

const fetcher = async (url: string): Promise<DocumentStatusSummary> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    return { total: 0, ready: 0, processing: 0, failed: 0, has_ready: false };
  }
  return res.json();
};

/**
 * Counts that power the empty-state / processing banner above the chat input.
 *
 * Instead of polling on a timer, we piggyback on the Supabase Realtime channel
 * the documents page already opens (`use-documents-realtime`). Any row event
 * on `documents` invalidates the SWR cache — so the banner flips to "hidden"
 * the instant the first doc reaches `ready`, with no extra HTTP traffic.
 *
 * Fallback: SWR revalidates on focus and on a generous 30s interval in case
 * the realtime channel ever drops (it self-heals, but we don't want a
 * permanently-stale banner if a websocket times out and re-subscription is
 * slow).
 */
export function useDocumentStatus() {
  const { organization } = useCurrentUser();
  const orgId = organization?.id ?? null;
  // Channel topics must be unique per subscriber — supabase-js rejects a
  // second `.on()` on a topic that's already entered the subscribing state,
  // and this hook is mounted by both the chat page and its banner.
  const instanceId = useId();

  const { data, error, isLoading, mutate } = useSWR<DocumentStatusSummary>(
    KEY,
    fetcher,
    {
      revalidateOnFocus: true,
      refreshInterval: 30_000,
      dedupingInterval: 1_000,
    },
  );

  useEffect(() => {
    if (!orgId) return;
    const supabase = createClient();
    const channel = supabase
      .channel(`document-status:org:${orgId}:${instanceId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "documents",
          filter: `org_id=eq.${orgId}`,
        },
        () => {
          // Any change to a document in this org → counts may have shifted.
          // SWR dedupes back-to-back revalidations within dedupingInterval.
          mutate();
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [orgId, instanceId, mutate]);

  return {
    status: data ?? null,
    loading: isLoading,
    error: error ? "failed to load document status" : null,
    refresh: mutate,
  };
}
