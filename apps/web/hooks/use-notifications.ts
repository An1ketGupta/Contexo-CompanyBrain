"use client";

import { useCallback } from "react";
import useSWR, { mutate as globalMutate } from "swr";

export interface Notification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  metadata: Record<string, unknown>;
  link_url: string | null;
  read_at: string | null;
  created_at: string;
}

interface ListResponse {
  notifications: Notification[];
}

interface CountResponse {
  count: number;
}

const fetcher = async <T>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed (${res.status})`);
  }
  return (await res.json()) as T;
};

// Refresh cadence chosen so a Sunday-night cron is visible by Monday-morning
// open without burning network on a sleeping tab.
const REFRESH_MS = 60_000;

interface UseNotificationsOptions {
  /** Page size. 20 for the bell popover; bump to 100 for the full-page view. */
  limit?: number;
}

/**
 * Bell-popover + badge data. Polls every 60s while focused, refetches on
 * focus, and exposes mark-read mutators that optimistically update SWR
 * caches so the badge count drops the moment the user clicks.
 */
export function useNotifications(options: UseNotificationsOptions = {}) {
  const limit = options.limit ?? 20;
  const listKey = `/api/notifications?limit=${limit}`;
  const countKey = "/api/notifications/unread-count";

  const { data: list, isLoading: listLoading, mutate: mutateList } = useSWR<
    ListResponse
  >(listKey, fetcher, {
    refreshInterval: REFRESH_MS,
    revalidateOnFocus: true,
    keepPreviousData: true,
  });

  const { data: count, mutate: mutateCount } = useSWR<CountResponse>(
    countKey,
    fetcher,
    {
      refreshInterval: REFRESH_MS,
      revalidateOnFocus: true,
      keepPreviousData: true,
    },
  );

  const markRead = useCallback(
    async (id: string) => {
      // Optimistic: flip read_at locally + drop the unread count by 1 so the
      // badge updates without waiting for the server roundtrip.
      const now = new Date().toISOString();
      await mutateList(
        (prev) =>
          prev
            ? {
                notifications: prev.notifications.map((n) =>
                  n.id === id && !n.read_at ? { ...n, read_at: now } : n,
                ),
              }
            : prev,
        { revalidate: false },
      );
      await mutateCount(
        (prev) => ({ count: Math.max(0, (prev?.count ?? 1) - 1) }),
        { revalidate: false },
      );

      try {
        const res = await fetch(`/api/notifications/${id}/read`, {
          method: "POST",
        });
        if (!res.ok && res.status !== 404) {
          throw new Error(`Failed (${res.status})`);
        }
      } catch {
        // Roll back by revalidating from the server. We swallow the error
        // because mark-read is best-effort and not user-blocking.
        await mutateList();
        await mutateCount();
      }
    },
    [mutateList, mutateCount],
  );

  const markAllRead = useCallback(async () => {
    const now = new Date().toISOString();
    await mutateList(
      (prev) =>
        prev
          ? {
              notifications: prev.notifications.map((n) =>
                n.read_at ? n : { ...n, read_at: now },
              ),
            }
          : prev,
      { revalidate: false },
    );
    await mutateCount(() => ({ count: 0 }), { revalidate: false });

    try {
      const res = await fetch("/api/notifications/mark-all-read", {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
    } catch {
      await mutateList();
      await mutateCount();
    }
  }, [mutateList, mutateCount]);

  return {
    notifications: list?.notifications ?? [],
    unreadCount: count?.count ?? 0,
    isLoading: listLoading,
    markRead,
    markAllRead,
    refresh: useCallback(() => {
      void globalMutate(listKey);
      void globalMutate(countKey);
    }, [listKey]),
  };
}
