"use client";

import { useCallback, useEffect, useState } from "react";

export type DocumentRecommendation = {
  key: string;
  name: string;
  description?: string;
  why?: string;
  examples?: string[];
  matched_document_id: string | null;
  matched_at: string | null;
  dismissed_at: string | null;
};

const REFRESH_EVENT = "recommendations:refresh";

export function refreshRecommendations(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
  }
}

/** V3 #50 — fetch the org's recommended-documents checklist. Hides itself
 *  when the org has no recommendations (pre-enrichment) or every entry is
 *  matched/dismissed.
 */
export function useRecommendations() {
  const [recommendations, setRecommendations] = useState<DocumentRecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/organizations/recommendations", {
        cache: "no-store",
      });
      if (!res.ok) {
        // 404 / 403 (no org yet) — render as empty list, not an error.
        if (res.status === 403 || res.status === 404) {
          setRecommendations([]);
          return;
        }
        throw new Error(`Failed to load recommendations (${res.status})`);
      }
      const body = (await res.json()) as { recommendations?: DocumentRecommendation[] };
      setRecommendations(body.recommendations ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load recommendations.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const handler = () => void load();
    window.addEventListener(REFRESH_EVENT, handler);
    return () => window.removeEventListener(REFRESH_EVENT, handler);
  }, [load]);

  const dismiss = useCallback(
    async (key: string) => {
      // Optimistic update — flip the local row immediately, refetch in the
      // background so we converge with whatever the server settled on.
      setRecommendations((prev) =>
        prev.map((r) =>
          r.key === key ? { ...r, dismissed_at: new Date().toISOString() } : r,
        ),
      );
      try {
        await fetch("/api/organizations/recommendations/dismiss", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key }),
        });
      } finally {
        void load();
      }
    },
    [load],
  );

  return { recommendations, loading, error, refresh: load, dismiss };
}
