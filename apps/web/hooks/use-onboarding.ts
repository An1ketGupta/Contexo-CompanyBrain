"use client";

import { useCallback } from "react";
import useSWR from "swr";
import type { OnboardingState } from "@/lib/types";

const KEY = "/api/organizations/onboarding";

const fetcher = async (url: string): Promise<OnboardingState> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    return {
      workspace_created: false,
      first_doc_uploaded: false,
      first_question_asked: false,
      completed: false,
      dismissed: true,
    };
  }
  return res.json();
};

/**
 * Read the derived onboarding checklist state.
 *
 * Steps are computed on the backend from real data (count of documents,
 * count of the caller's messages). The only persisted bit is `dismissed`,
 * stored on `organizations.metadata->onboarding.dismissed`.
 *
 * Revalidation runs on focus and at a slow 15s tick — fast enough that
 * "upload a doc → see it tick off" feels live without thrashing the
 * three-count endpoint. Realtime invalidation of the doc count is the
 * documents-channel subscription on the chat page; messages take effect on
 * the next focus / interval, which is fine — the user just sent one, so
 * they aren't reading the banner.
 */
export function useOnboarding() {
  const { data, mutate, isLoading } = useSWR<OnboardingState>(KEY, fetcher, {
    revalidateOnFocus: true,
    refreshInterval: 15_000,
    dedupingInterval: 2_000,
  });

  const dismiss = useCallback(async () => {
    // Optimistic: hide immediately, fall back to the server's truth on error.
    await mutate(
      (prev) =>
        prev
          ? { ...prev, dismissed: true }
          : {
              workspace_created: false,
              first_doc_uploaded: false,
              first_question_asked: false,
              completed: false,
              dismissed: true,
            },
      { revalidate: false },
    );
    try {
      await fetch("/api/organizations/onboarding/dismiss", { method: "POST" });
    } finally {
      await mutate();
    }
  }, [mutate]);

  return {
    state: data ?? null,
    loading: isLoading,
    dismiss,
    refresh: mutate,
  };
}
