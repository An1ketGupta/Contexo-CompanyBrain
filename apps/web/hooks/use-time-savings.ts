"use client";

import useSWR from "swr";
import { networkError, parseApiError, type ApiError } from "@/lib/errors";

export interface MyTimeSavings {
  this_week: number;
  this_month: number;
  all_time: number;
  hours_this_month: number;
}

const fetcher = async (url: string): Promise<MyTimeSavings> => {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    throw networkError(err);
  }
  if (!res.ok) throw await parseApiError(res);
  return res.json();
};

export function useMyTimeSavings() {
  const { data, error, isLoading, mutate } = useSWR<MyTimeSavings, ApiError>(
    "/api/me/time-savings",
    fetcher,
    {
      revalidateOnFocus: false,
      // Quiet polling — savings move slowly. One refresh per 60s is plenty.
      refreshInterval: 60_000,
    },
  );

  return {
    savings: data ?? null,
    loading: isLoading,
    error: error ? error.message : null,
    refresh: mutate,
  };
}
