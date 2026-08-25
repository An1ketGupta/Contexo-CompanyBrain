"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, CopyCheck, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface DuplicateMatch {
  doc_id: string;
  doc_name: string;
  similarity: number;
}

interface DuplicateRow {
  id: string;
  title: string;
  body: string | null;
  document_id: string;
  match_document_id: string;
  similarity: number;
  matches: DuplicateMatch[];
  created_at: string;
}

interface ListResponse {
  duplicates: DuplicateRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function DuplicatesPage() {
  const [running, setRunning] = useState(false);
  const { data, error, isLoading, mutate } = useSWR<ListResponse>(
    "/api/admin/duplicates",
    fetcher,
    { revalidateOnFocus: false },
  );

  const dismiss = async (id: string) => {
    try {
      const res = await fetch(`/api/admin/duplicates/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      mutate();
      toast.success("Duplicate dismissed.");
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  const runBackfill = async () => {
    setRunning(true);
    try {
      const res = await fetch("/api/admin/duplicates/backfill", { method: "POST" });
      if (!res.ok && res.status !== 202) throw new Error(`Failed (${res.status})`);
      toast.success("Backfill queued. Detections will surface as the job completes.");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Possible duplicates</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Documents the ingest pipeline flagged as near-identical to an existing doc
            in your knowledge base. Review and decide whether to merge or keep both.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={runBackfill}
          disabled={running}
          className="gap-2"
        >
          <RefreshCw className={running ? "size-4 animate-spin" : "size-4"} />
          {running ? "Backfilling…" : "Backfill legacy docs"}
        </Button>
      </header>

      {error ? (
        <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : !data?.duplicates.length ? (
        <div className="rounded-md border border-dashed p-10 text-center">
          <CopyCheck className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No duplicates flagged.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            New documents are scanned after they finish ingestion. Legacy documents need
            a backfill to be included.
          </p>
        </div>
      ) : (
        <ul className="space-y-3">
          {data.duplicates.map((row) => (
            <li key={row.id} className="rounded-md border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-sm font-medium">{row.title}</h2>
                  {row.body && (
                    <p className="mt-1 text-xs text-muted-foreground">{row.body}</p>
                  )}
                  <div className="mt-3 flex items-center gap-2 text-xs">
                    <Badge variant="default">
                      {(row.similarity * 100).toFixed(0)}% match
                    </Badge>
                    <span className="text-muted-foreground">
                      flagged {new Date(row.created_at).toLocaleString()}
                    </span>
                  </div>
                  {row.matches.length > 1 && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-muted-foreground">
                        {row.matches.length - 1} more close match
                        {row.matches.length - 1 === 1 ? "" : "es"}
                      </summary>
                      <ul className="mt-1 space-y-0.5 pl-3">
                        {row.matches.slice(1).map((m) => (
                          <li key={m.doc_id}>
                            {(m.similarity * 100).toFixed(0)}% — {m.doc_name}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => dismiss(row.id)}
                  aria-label="Dismiss"
                  className="text-xs"
                >
                  <X className="mr-1 size-3" /> Dismiss
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
