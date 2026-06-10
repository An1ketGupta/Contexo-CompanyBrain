"use client";

import useSWR from "swr";
import type { UsageSnapshot } from "@/lib/types";

const fetcher = async (url: string): Promise<UsageSnapshot> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load usage (${res.status})`);
  return res.json();
};

export function useUsage() {
  // Refresh every minute. Users keep this in the sidebar; we want the meter
  // to walk forward without them needing to reload. The endpoint hits Upstash
  // GET (no INCR), so polling is cheap.
  const { data, error, mutate } = useSWR<UsageSnapshot>("/api/usage/me", fetcher, {
    refreshInterval: 60_000,
    revalidateOnFocus: true,
  });
  return {
    usage: data ?? null,
    error,
    refresh: mutate,
  };
}
