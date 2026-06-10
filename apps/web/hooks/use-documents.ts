"use client";

import { useCallback, useMemo } from "react";
import useSWR from "swr";
import type { Document, DocumentTag } from "@/lib/types";
import { networkError, parseApiError, type ApiError } from "@/lib/errors";

interface DocumentsResponse {
  documents: Document[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentFilters {
  status: string;        // "" | pending | processing | ready | failed
  file_type: string;     // "" | pdf | docx | txt | md
  tags: string[];
  search: string;
  date_from: string;     // YYYY-MM-DD or ""
  date_to: string;
  sort_by: "created_at" | "name" | "file_size_bytes";
  sort_dir: "asc" | "desc";
}

export const DEFAULT_FILTERS: DocumentFilters = {
  status: "",
  file_type: "",
  tags: [],
  search: "",
  date_from: "",
  date_to: "",
  sort_by: "created_at",
  sort_dir: "desc",
};

export function isFiltering(f: DocumentFilters): boolean {
  return (
    !!f.status ||
    !!f.file_type ||
    !!f.search ||
    !!f.date_from ||
    !!f.date_to ||
    f.tags.length > 0 ||
    f.sort_by !== "created_at" ||
    f.sort_dir !== "desc"
  );
}

function buildKey(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.file_type) params.set("file_type", filters.file_type);
  if (filters.search) params.set("search", filters.search);
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) {
    // Inclusive end-of-day: the API treats `date_to` as <= cutoff, and a date
    // input "2024-02-15" becomes 00:00 UTC. Bump to 23:59 so the natural
    // "include this day" semantics hold.
    const d = new Date(`${filters.date_to}T23:59:59Z`);
    if (!Number.isNaN(d.getTime())) params.set("date_to", d.toISOString());
  }
  for (const t of filters.tags) params.append("tag", t);
  params.set("sort_by", filters.sort_by);
  params.set("sort_dir", filters.sort_dir);
  params.set("limit", "200");
  const qs = params.toString();
  return `/api/documents${qs ? `?${qs}` : ""}`;
}

const fetcher = async (url: string): Promise<DocumentsResponse> => {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    throw networkError(err);
  }
  if (!res.ok) throw await parseApiError(res);
  return res.json();
};

const tagsFetcher = async (url: string): Promise<{ tags: DocumentTag[] }> => {
  const res = await fetch(url);
  if (!res.ok) return { tags: [] };
  return res.json();
};

export function useDocuments(filters: DocumentFilters = DEFAULT_FILTERS) {
  const key = useMemo(() => buildKey(filters), [filters]);
  const { data, error, isLoading, mutate } = useSWR<DocumentsResponse, ApiError>(
    key,
    fetcher,
    {
      revalidateOnFocus: true,
      keepPreviousData: true,
    },
  );

  const documents = data?.documents ?? [];
  const total = data?.total ?? 0;

  const refresh = useCallback(() => mutate(), [mutate]);

  const deleteDocument = useCallback(
    async (id: string): Promise<void> => {
      const previous = data;
      await mutate(
        previous
          ? { ...previous, documents: previous.documents.filter((d) => d.id !== id) }
          : previous,
        { revalidate: false },
      );

      const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        await mutate(previous, { revalidate: false });
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? `Delete failed (${res.status})`);
      }
      await mutate();
    },
    [data, mutate],
  );

  const bulkDelete = useCallback(
    async (ids: string[]): Promise<{ deleted: number; skipped: number }> => {
      const previous = data;
      const idSet = new Set(ids);
      await mutate(
        previous
          ? {
              ...previous,
              documents: previous.documents.filter((d) => !idSet.has(d.id)),
            }
          : previous,
        { revalidate: false },
      );

      let res: Response;
      try {
        res = await fetch("/api/documents/bulk", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ document_ids: ids }),
        });
      } catch (err) {
        await mutate(previous, { revalidate: false });
        throw networkError(err);
      }
      if (!res.ok) {
        await mutate(previous, { revalidate: false });
        throw await parseApiError(res);
      }
      const out = (await res.json()) as { deleted: number; skipped: number };
      await mutate();
      return out;
    },
    [data, mutate],
  );

  const bulkAddTags = useCallback(
    async (
      ids: string[],
      tags: string[],
    ): Promise<{ updated: number; tags_applied: string[] }> => {
      let res: Response;
      try {
        res = await fetch("/api/documents/bulk/tags", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ document_ids: ids, tags }),
        });
      } catch (err) {
        throw networkError(err);
      }
      if (!res.ok) throw await parseApiError(res);
      const out = (await res.json()) as { updated: number; tags_applied: string[] };
      await mutate();
      return out;
    },
    [mutate],
  );

  const updateTags = useCallback(
    async (id: string, tags: string[]): Promise<void> => {
      const res = await fetch(`/api/documents/${id}/tags`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags }),
      });
      if (!res.ok) throw await parseApiError(res);
      await mutate();
    },
    [mutate],
  );

  const upsertDocument = useCallback(
    (doc: Document) => {
      mutate(
        (current) => {
          if (!current) return current;
          const idx = current.documents.findIndex((d) => d.id === doc.id);
          if (idx === -1) {
            return { ...current, documents: [doc, ...current.documents] };
          }
          const next = [...current.documents];
          next[idx] = { ...next[idx], ...doc };
          return { ...current, documents: next };
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
            ? {
                ...current,
                documents: current.documents.filter((d) => d.id !== id),
              }
            : current,
        { revalidate: false },
      );
    },
    [mutate],
  );

  const retryDocument = useCallback(
    async (id: string): Promise<void> => {
      const previous = data;
      await mutate(
        previous
          ? {
              ...previous,
              documents: previous.documents.map((d) =>
                d.id === id
                  ? { ...d, status: "pending" as const, chunk_count: null }
                  : d,
              ),
            }
          : previous,
        { revalidate: false },
      );

      const res = await fetch(`/api/documents/${id}/reprocess`, {
        method: "POST",
      });
      if (!res.ok && res.status !== 202) {
        await mutate(previous, { revalidate: false });
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? `Retry failed (${res.status})`);
      }
    },
    [data, mutate],
  );

  return {
    documents,
    total,
    loading: isLoading,
    error: error ? error.message : null,
    refresh,
    deleteDocument,
    bulkDelete,
    bulkAddTags,
    updateTags,
    upsertDocument,
    removeDocument,
    retryDocument,
  };
}

export function useDocumentTags() {
  const { data, mutate } = useSWR<{ tags: DocumentTag[] }>(
    "/api/documents/tags",
    tagsFetcher,
    { revalidateOnFocus: false },
  );
  return { tags: data?.tags ?? [], refresh: mutate };
}
