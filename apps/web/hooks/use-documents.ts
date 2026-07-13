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
  kind: string;           // "" | meeting_transcript
  tags: string[];
  search: string;
  sort_by: "created_at" | "name" | "file_size_bytes";
  sort_dir: "asc" | "desc";
}

export const DEFAULT_FILTERS: DocumentFilters = {
  status: "",
  file_type: "",
  kind: "",
  tags: [],
  search: "",
  sort_by: "created_at",
  sort_dir: "desc",
};

export function isFiltering(f: DocumentFilters): boolean {
  return (
    !!f.status ||
    !!f.file_type ||
    !!f.kind ||
    !!f.search ||
    f.tags.length > 0 ||
    f.sort_by !== "created_at" ||
    f.sort_dir !== "desc"
  );
}

function buildKey(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.file_type) params.set("file_type", filters.file_type);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.search) params.set("search", filters.search);
  for (const t of filters.tags) params.append("tag", t);
  params.set("sort_by", filters.sort_by);
  params.set("sort_dir", filters.sort_dir);
  params.set("limit", "200");
  const qs = params.toString();
  return `/api/documents${qs ? `?${qs}` : ""}`;
}

// Secondary key: same filters but no search term, exact tag match on the
// search term instead. Only active when search is non-empty.
function buildTagSearchKey(filters: DocumentFilters): string | null {
  if (!filters.search) return null;
  const normalised = filters.search.trim().toLowerCase();
  if (!normalised) return null;
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.file_type) params.set("file_type", filters.file_type);
  if (filters.kind) params.set("kind", filters.kind);
  params.append("tag", normalised);
  params.set("sort_by", filters.sort_by);
  params.set("sort_dir", filters.sort_dir);
  params.set("limit", "200");
  return `/api/documents?${params.toString()}`;
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
  const tagKey = useMemo(() => buildTagSearchKey(filters), [filters]);

  const { data, error, isLoading, mutate } = useSWR<DocumentsResponse, ApiError>(
    key,
    fetcher,
    { revalidateOnFocus: true, keepPreviousData: true },
  );

  // Secondary fetch: tag-exact matches for the search term. Runs in parallel
  // with the name search; results are appended after name matches.
  const { data: tagData } = useSWR<DocumentsResponse, ApiError>(
    tagKey,
    fetcher,
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const documents = useMemo(() => {
    const nameMatches = data?.documents ?? [];
    if (!tagData?.documents?.length) return nameMatches;
    // Append tag-only matches that didn't appear in the name search.
    const seen = new Set(nameMatches.map((d) => d.id));
    const tagOnly = tagData.documents.filter((d) => !seen.has(d.id));
    return [...nameMatches, ...tagOnly];
  }, [data, tagData]);

  const total = (data?.total ?? 0) + (tagData?.documents
    ? tagData.documents.filter(
        (d) => !(data?.documents ?? []).some((n) => n.id === d.id),
      ).length
    : 0);

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

  const updateVisibility = useCallback(
    async (id: string, visibility: "private" | "org"): Promise<void> => {
      const res = await fetch(`/api/documents/${id}/visibility`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ visibility }),
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
    updateVisibility,
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
