"use client";

import { useCallback } from "react";
import useSWR, { useSWRConfig } from "swr";
import { networkError, parseApiError, type ApiError } from "@/lib/errors";

// Archive/restore touches multiple conversation list views (sidebar, archive
// page, archived count). They all share the same `/api/chat/conversations`
// URL prefix but with different query strings, so each is a distinct SWR
// cache key. After a successful archive/restore, invalidate every matching
// key so views in other parts of the dashboard refresh without waiting for
// the focus-revalidate or 60s tick.
const isConversationListKey = (key: unknown): boolean =>
  typeof key === "string" && key.startsWith("/api/chat/conversations");

export interface ConversationSummary {
  id: string;
  title: string | null;
  is_pinned: boolean;
  // V3 #104 — archived rows are hidden from the default list. The flag rides
  // on search results so the sidebar can render an "Archived" badge on hits.
  is_archived?: boolean;
  archived_at?: string | null;
  archive_reason?: string | null;
  created_at: string;
  updated_at: string;
}

interface ConversationsResponse {
  conversations: ConversationSummary[];
}

export interface UseConversationsOptions {
  // When true, calls the list endpoint with ?archived_only=true. Used by the
  // /archive page; the sidebar leaves this off.
  archivedOnly?: boolean;
}

const fetcher = async (url: string): Promise<ConversationsResponse> => {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    throw networkError(err);
  }
  if (!res.ok) throw await parseApiError(res);
  return res.json();
};

export function useConversations(
  query?: string,
  options?: UseConversationsOptions,
) {
  const trimmed = (query ?? "").trim();
  const archivedOnly = options?.archivedOnly === true;
  const params = new URLSearchParams();
  if (trimmed) {
    params.set("q", trimmed);
    // Searches should surface archived hits too — the row renders an Archive
    // badge so the user can tell them apart. Skip this when we're explicitly
    // scoped to archived-only (the backend already returns only archived).
    if (!archivedOnly) params.set("include_archived", "true");
  }
  if (archivedOnly) {
    params.set("archived_only", "true");
    params.set("limit", "200");
  }
  const qs = params.toString();
  const url = qs
    ? `/api/chat/conversations?${qs}`
    : "/api/chat/conversations";
  const { data, error, isLoading, mutate } = useSWR<ConversationsResponse, ApiError>(
    url,
    fetcher,
    {
      revalidateOnFocus: true,
      // Keep the last result while a new search is in flight so the sidebar
      // doesn't flash empty between keystrokes.
      keepPreviousData: true,
    },
  );
  const { mutate: globalMutate } = useSWRConfig();

  const conversations = data?.conversations ?? [];

  const refresh = useCallback(() => mutate(), [mutate]);

  // Optimistically place a brand new conversation at the top of the list while
  // we wait for the first server-side update_at bump.
  const prepend = useCallback(
    (id: string, title: string) => {
      mutate(
        (current) => {
          const list = current?.conversations ?? [];
          if (list.some((c) => c.id === id)) return current;
          const now = new Date().toISOString();
          return {
            conversations: [
              { id, title, is_pinned: false, created_at: now, updated_at: now },
              ...list,
            ],
          };
        },
        { revalidate: false },
      );
    },
    [mutate],
  );

  // Toggle the pinned flag. Optimistic — rolls back on PATCH failure.
  // We sort pinned-first locally too so the row jumps into the right
  // section without waiting for the SWR refetch.
  const setPinned = useCallback(
    async (id: string, isPinned: boolean): Promise<void> => {
      const previous = data;
      mutate(
        (current) => {
          if (!current) return current;
          const next = current.conversations.map((c) =>
            c.id === id ? { ...c, is_pinned: isPinned } : c,
          );
          next.sort((a, b) => {
            if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1;
            return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
          });
          return { conversations: next };
        },
        { revalidate: false },
      );

      let res: Response;
      try {
        res = await fetch(`/api/chat/conversations/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_pinned: isPinned }),
        });
      } catch (err) {
        await mutate(previous, { revalidate: false });
        throw networkError(err);
      }
      if (!res.ok) {
        await mutate(previous, { revalidate: false });
        throw await parseApiError(res);
      }
    },
    [data, mutate],
  );

  const rename = useCallback(
    async (id: string, title: string): Promise<void> => {
      const previous = data;
      mutate(
        (current) => {
          if (!current) return current;
          return {
            conversations: current.conversations.map((c) =>
              c.id === id ? { ...c, title } : c,
            ),
          };
        },
        { revalidate: false },
      );

      let res: Response;
      try {
        res = await fetch(`/api/chat/conversations/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title }),
        });
      } catch (err) {
        await mutate(previous, { revalidate: false });
        throw networkError(err);
      }
      if (!res.ok) {
        await mutate(previous, { revalidate: false });
        throw await parseApiError(res);
      }
    },
    [data, mutate],
  );

  const remove = useCallback(
    async (id: string): Promise<void> => {
      const previous = data;
      await mutate(
        previous
          ? { conversations: previous.conversations.filter((c) => c.id !== id) }
          : previous,
        { revalidate: false },
      );

      let res: Response;
      try {
        res = await fetch(`/api/chat/conversations/${id}`, {
          method: "DELETE",
        });
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

  // Bump the conversation's updated_at locally so it re-sorts to top
  // immediately after a send (without waiting for the next SWR revalidation).
  const touch = useCallback(
    (id: string) => {
      mutate(
        (current) => {
          if (!current) return current;
          const now = new Date().toISOString();
          const idx = current.conversations.findIndex((c) => c.id === id);
          if (idx === -1) return current;
          const next = [...current.conversations];
          const [moved] = next.splice(idx, 1);
          next.unshift({ ...moved, updated_at: now });
          return { conversations: next };
        },
        { revalidate: false },
      );
    },
    [mutate],
  );

  // V3 #104 — archive removes the row from the active sidebar list. We
  // optimistically drop it locally and revalidate from the server on failure.
  // The server enforces "no archive while pinned" via a DB trigger; a 409 here
  // is the friendly UX prompt to unpin first.
  const archive = useCallback(
    async (id: string): Promise<void> => {
      const previous = data;
      mutate(
        (current) => {
          if (!current) return current;
          if (archivedOnly) return current; // not in this list to begin with
          return {
            conversations: current.conversations.filter((c) => c.id !== id),
          };
        },
        { revalidate: false },
      );
      let res: Response;
      try {
        res = await fetch(`/api/chat/conversations/${id}/archive`, {
          method: "POST",
        });
      } catch (err) {
        await mutate(previous, { revalidate: false });
        throw networkError(err);
      }
      if (!res.ok) {
        await mutate(previous, { revalidate: false });
        throw await parseApiError(res);
      }
      await globalMutate(isConversationListKey);
    },
    [archivedOnly, data, mutate, globalMutate],
  );

  const restore = useCallback(
    async (id: string): Promise<void> => {
      const previous = data;
      mutate(
        (current) => {
          if (!current) return current;
          if (archivedOnly) {
            // On the archive page: drop the row optimistically.
            return {
              conversations: current.conversations.filter((c) => c.id !== id),
            };
          }
          return current;
        },
        { revalidate: false },
      );
      let res: Response;
      try {
        res = await fetch(`/api/chat/conversations/${id}/restore`, {
          method: "POST",
        });
      } catch (err) {
        await mutate(previous, { revalidate: false });
        throw networkError(err);
      }
      if (!res.ok) {
        await mutate(previous, { revalidate: false });
        throw await parseApiError(res);
      }
      await globalMutate(isConversationListKey);
    },
    [archivedOnly, data, mutate, globalMutate],
  );

  // Bulk operations skip optimistic updates because partial failures are real
  // (pinned rows get filtered server-side). We refresh after the request
  // completes so the UI reflects what actually changed.
  const bulkArchive = useCallback(
    async (ids: string[]): Promise<BulkArchiveResponse> => {
      const res = await fetch("/api/chat/conversations/archive/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_ids: ids }),
      });
      if (!res.ok) throw await parseApiError(res);
      const payload = (await res.json()) as BulkArchiveResponse;
      await globalMutate(isConversationListKey);
      return payload;
    },
    [globalMutate],
  );

  const bulkRestore = useCallback(
    async (ids: string[]): Promise<BulkRestoreResponse> => {
      const res = await fetch("/api/chat/conversations/archive/bulk-restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_ids: ids }),
      });
      if (!res.ok) throw await parseApiError(res);
      const payload = (await res.json()) as BulkRestoreResponse;
      await globalMutate(isConversationListKey);
      return payload;
    },
    [globalMutate],
  );

  return {
    conversations,
    loading: isLoading,
    error: error ? error.message : null,
    refresh,
    prepend,
    rename,
    remove,
    touch,
    setPinned,
    archive,
    restore,
    bulkArchive,
    bulkRestore,
  };
}


export interface BulkArchiveResponse {
  archived_count: number;
  skipped_count: number;
  archived_ids: string[];
  skipped_ids: string[];
}

export interface BulkRestoreResponse {
  restored_count: number;
  restored_ids: string[];
}


// Cheap COUNT(*) for the "Archived (N)" sidebar pill. Revalidates on focus
// so the count stays fresh as the user archives/restores from any tab.
export function useArchivedCount() {
  const { data, mutate } = useSWR<{ count: number }>(
    "/api/chat/conversations/archive/count",
    async (url: string) => {
      const res = await fetch(url);
      if (!res.ok) throw await parseApiError(res);
      return res.json();
    },
    { revalidateOnFocus: true, refreshInterval: 60_000 },
  );
  return {
    count: data?.count ?? 0,
    refresh: () => mutate(),
  };
}
