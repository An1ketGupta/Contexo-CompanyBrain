"use client";

import { useCallback } from "react";
import useSWR from "swr";
import type { Document } from "@/lib/types";

interface DocumentsResponse {
  documents: Document[];
}

const fetcher = async (url: string): Promise<DocumentsResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load documents (${res.status})`);
  return res.json();
};

export function useDocuments() {
  const { data, error, isLoading, mutate } = useSWR<DocumentsResponse>(
    "/api/documents",
    fetcher,
    {
      revalidateOnFocus: true,
      // Realtime hook owns push-updates; SWR is the source of truth for the
      // initial fetch + cache. No interval polling — Realtime handles deltas.
    },
  );

  const documents = data?.documents ?? [];

  const refresh = useCallback(() => mutate(), [mutate]);

  const deleteDocument = useCallback(
    async (id: string): Promise<void> => {
      // Optimistic removal — revert if the server rejects.
      const previous = data;
      await mutate(
        previous
          ? { documents: previous.documents.filter((d) => d.id !== id) }
          : previous,
        { revalidate: false },
      );

      const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        await mutate(previous, { revalidate: false });
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.error ?? `Delete failed (${res.status})`);
      }

      // Sync with server-side truth after the fact.
      await mutate();
    },
    [data, mutate],
  );

  /** Merge a single document update (typically from Realtime) into the cache. */
  const upsertDocument = useCallback(
    (doc: Document) => {
      mutate(
        (current) => {
          const list = current?.documents ?? [];
          const idx = list.findIndex((d) => d.id === doc.id);
          if (idx === -1) return { documents: [doc, ...list] };
          const next = [...list];
          next[idx] = { ...next[idx], ...doc };
          return { documents: next };
        },
        { revalidate: false },
      );
    },
    [mutate],
  );

  const removeDocument = useCallback(
    (id: string) => {
      mutate(
        (current) =>
          current
            ? { documents: current.documents.filter((d) => d.id !== id) }
            : current,
        { revalidate: false },
      );
    },
    [mutate],
  );

  return {
    documents,
    loading: isLoading,
    error: error ? (error as Error).message : null,
    refresh,
    deleteDocument,
    upsertDocument,
    removeDocument,
  };
}
