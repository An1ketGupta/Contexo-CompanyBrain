"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { networkError, parseApiError, type ApiError } from "@/lib/errors";

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

interface ConversationsResponse {
  conversations: ConversationSummary[];
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

export function useConversations() {
  const { data, error, isLoading, mutate } = useSWR<ConversationsResponse, ApiError>(
    "/api/chat/conversations",
    fetcher,
    { revalidateOnFocus: true },
  );

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
              { id, title, created_at: now, updated_at: now },
              ...list,
            ],
          };
        },
        { revalidate: false },
      );
    },
    [mutate],
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

  return {
    conversations,
    loading: isLoading,
    error: error ? error.message : null,
    refresh,
    prepend,
    rename,
    remove,
    touch,
  };
}
