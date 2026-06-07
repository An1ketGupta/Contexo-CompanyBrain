"use client";

import { useState, useEffect, useCallback } from "react";
import type { Document } from "@/lib/types";

interface UseDocumentsReturn {
  documents: Document[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  deleteDocument: (id: string) => Promise<void>;
}

export function useDocuments(): UseDocumentsReturn {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch("/api/documents")
      .then((res) => {
        if (!res.ok) throw new Error(`Failed to load documents (${res.status})`);
        return res.json();
      })
      .then(({ documents: docs }: { documents: Document[] }) => {
        if (!cancelled) setDocuments(docs);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [tick]);

  const deleteDocument = useCallback(
    async (id: string): Promise<void> => {
      await fetch(`/api/documents/${id}`, { method: "DELETE" });
      setDocuments((prev) => prev.filter((d) => d.id !== id));
    },
    [],
  );

  return { documents, loading, error, refresh, deleteDocument };
}
