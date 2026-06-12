"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { networkError, parseApiError, type ApiError } from "@/lib/errors";

export interface Collection {
  id: string;
  name: string;
  description: string | null;
  color: string;
  icon: string | null;
  tag_filters: string[];
  document_count: number;
  created_at: string | null;
  updated_at: string | null;
}

interface CollectionsResponse {
  collections: Collection[];
}

export interface CollectionUpsertInput {
  name: string;
  description?: string | null;
  color?: string;
  icon?: string | null;
  tag_filters: string[];
}

const fetcher = async (url: string): Promise<CollectionsResponse> => {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    throw networkError(err);
  }
  if (!res.ok) throw await parseApiError(res);
  return res.json();
};

export function useCollections() {
  const { data, error, isLoading, mutate } = useSWR<CollectionsResponse, ApiError>(
    "/api/collections",
    fetcher,
    {
      revalidateOnFocus: false,
      // Collections change rarely; we want the chat surface to feel snappy
      // without thrashing every focus change. Manual refresh after mutations.
      dedupingInterval: 30_000,
    },
  );

  const collections = data?.collections ?? [];
  const refresh = useCallback(() => mutate(), [mutate]);

  const create = useCallback(
    async (input: CollectionUpsertInput): Promise<Collection> => {
      let res: Response;
      try {
        res = await fetch("/api/collections", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        });
      } catch (err) {
        throw networkError(err);
      }
      if (!res.ok) throw await parseApiError(res);
      const created = (await res.json()) as Collection;
      // Optimistic: insert into cache so the new pill appears immediately.
      await mutate(
        (curr) => ({
          collections: [...(curr?.collections ?? []), created].sort((a, b) =>
            a.name.localeCompare(b.name),
          ),
        }),
        { revalidate: false },
      );
      return created;
    },
    [mutate],
  );

  const update = useCallback(
    async (id: string, patch: Partial<CollectionUpsertInput>): Promise<Collection> => {
      let res: Response;
      try {
        res = await fetch(`/api/collections/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patch),
        });
      } catch (err) {
        throw networkError(err);
      }
      if (!res.ok) throw await parseApiError(res);
      const updated = (await res.json()) as Collection;
      await mutate(
        (curr) => ({
          collections: (curr?.collections ?? [])
            .map((c) => (c.id === id ? updated : c))
            .sort((a, b) => a.name.localeCompare(b.name)),
        }),
        { revalidate: false },
      );
      return updated;
    },
    [mutate],
  );

  const remove = useCallback(
    async (id: string): Promise<void> => {
      const previous = data;
      await mutate(
        previous
          ? { collections: previous.collections.filter((c) => c.id !== id) }
          : previous,
        { revalidate: false },
      );
      let res: Response;
      try {
        res = await fetch(`/api/collections/${id}`, { method: "DELETE" });
      } catch (err) {
        await mutate(previous, { revalidate: false });
        throw networkError(err);
      }
      if (!res.ok && res.status !== 204) {
        await mutate(previous, { revalidate: false });
        throw await parseApiError(res);
      }
    },
    [data, mutate],
  );

  return {
    collections,
    loading: isLoading,
    error: error ? error.message : null,
    refresh,
    create,
    update,
    remove,
  };
}
