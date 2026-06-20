"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type QueryHistoryEntry = {
  id: string;
  conversation_id: string | null;
  message_id: string | null;
  query_text: string;
  intent: string | null;
  response_length: number;
  source_count: number;
  tool_calls: number;
  latency_ms: number | null;
  model_used: string | null;
  created_at: string;
};

export type QueryHistoryFilters = {
  intent?: string | null;
  search?: string | null;
};

const PAGE_SIZE = 20;

/** V3 #91 — cursor-paginated query history. Filters trigger a clean re-fetch
 *  (cursor is reset). loadMore appends. The deduped-by-id reducer survives
 *  React Strict Mode double-effects without producing duplicate rows.
 */
export function useQueryHistory(filters: QueryHistoryFilters) {
  const [entries, setEntries] = useState<QueryHistoryEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Used to ignore late responses from a previous filter set.
  const requestSeq = useRef(0);

  const buildQs = useCallback(
    (cur: string | null) => {
      const qs = new URLSearchParams();
      qs.set("limit", String(PAGE_SIZE));
      if (cur) qs.set("cursor", cur);
      if (filters.intent) qs.set("intent", filters.intent);
      if (filters.search) qs.set("search", filters.search);
      return qs.toString();
    },
    [filters.intent, filters.search],
  );

  const loadInitial = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/me/query-history?${buildQs(null)}`, {
        cache: "no-store",
      });
      if (!res.ok) {
        throw new Error(`Failed to load history (${res.status})`);
      }
      const body = (await res.json()) as {
        entries: QueryHistoryEntry[];
        next_cursor: string | null;
      };
      if (seq !== requestSeq.current) return;
      setEntries(body.entries ?? []);
      setCursor(body.next_cursor);
      setHasMore(Boolean(body.next_cursor));
    } catch (e) {
      if (seq !== requestSeq.current) return;
      setError(e instanceof Error ? e.message : "Failed to load history.");
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [buildQs]);

  const loadMore = useCallback(async () => {
    if (!cursor || loading) return;
    const seq = requestSeq.current; // Don't bump — this is a continuation.
    setLoading(true);
    try {
      const res = await fetch(`/api/me/query-history?${buildQs(cursor)}`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Failed to load more (${res.status})`);
      const body = (await res.json()) as {
        entries: QueryHistoryEntry[];
        next_cursor: string | null;
      };
      if (seq !== requestSeq.current) return;
      setEntries((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        const next = (body.entries ?? []).filter((e) => !seen.has(e.id));
        return [...prev, ...next];
      });
      setCursor(body.next_cursor);
      setHasMore(Boolean(body.next_cursor));
    } catch (e) {
      if (seq !== requestSeq.current) return;
      setError(e instanceof Error ? e.message : "Failed to load more.");
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [buildQs, cursor, loading]);

  useEffect(() => {
    void loadInitial();
  }, [loadInitial]);

  return { entries, hasMore, loading, error, loadMore, refresh: loadInitial };
}
