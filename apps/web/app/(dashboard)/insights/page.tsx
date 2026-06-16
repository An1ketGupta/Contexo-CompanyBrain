"use client";

import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, TrendingUp } from "lucide-react";
import { FileIcon } from "@/components/documents/file-icon";
import { Skeleton } from "@/components/ui/skeleton";
import type { DocumentFileType } from "@/lib/types";

interface InsightsResponse {
  top_documents: {
    id: string;
    name: string;
    file_type: DocumentFileType;
    citation_count: number;
    last_cited_at: string | null;
    created_at: string;
  }[];
  unused_documents: {
    id: string;
    name: string;
    file_type: DocumentFileType;
    citation_count: number;
    created_at: string;
  }[];
  totals: {
    documents: number;
    ready_documents: number;
    cited_documents: number;
    citations: number;
  };
}

const fetcher = async (url: string): Promise<InsightsResponse> => {
  const res = await fetch(url);
  if (res.status === 403) {
    throw new Error("Admin access required.");
  }
  if (!res.ok) {
    throw new Error(`Failed to load (${res.status})`);
  }
  return res.json();
};

export default function InsightsPage() {
  const { data, error, isLoading } = useSWR<InsightsResponse>(
    "/api/usage/knowledge-intelligence",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">
          Knowledge intelligence
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Which documents the AI is actually drawing from — and which ones may
          be dead weight.
        </p>
      </header>

      {isLoading ? (
        <InsightsSkeleton />
      ) : error ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive-soft px-4 py-3 text-sm text-destructive">
          {(error as Error).message}
        </div>
      ) : data ? (
        <>
          <Totals data={data.totals} />

          <section>
            <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
              <TrendingUp className="h-4 w-4 text-emerald-600" />
              Most cited
            </h2>
            {data.top_documents.length === 0 ? (
              <p className="rounded-lg border border-border bg-background px-4 py-3 text-sm text-muted-foreground">
                Nothing cited yet. As the team chats, this list fills in.
              </p>
            ) : (
              <ol className="divide-y divide-border rounded-lg border border-border bg-background">
                {data.top_documents.map((doc, i) => (
                  <li key={doc.id} className="flex items-center gap-3 px-4 py-3">
                    <span className="w-5 text-right text-xs font-medium tabular-nums text-muted-foreground">
                      {i + 1}
                    </span>
                    <FileIcon
                      type={doc.file_type}
                      className="h-4 w-4 shrink-0 text-muted-foreground"
                    />
                    <Link
                      href={`/chat?document_id=${encodeURIComponent(doc.id)}`}
                      className="min-w-0 flex-1 truncate text-sm font-medium hover:underline"
                    >
                      {doc.name}
                    </Link>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {doc.citation_count.toLocaleString()} citation
                      {doc.citation_count === 1 ? "" : "s"}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section>
            <h2 className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              Never cited
            </h2>
            <p className="mb-3 text-xs text-muted-foreground">
              These documents have been indexed but no chat turn has drawn from
              them. They may be stale, off-topic, or missing the keywords the
              team actually uses.
            </p>
            {data.unused_documents.length === 0 ? (
              <p className="rounded-lg border border-border bg-background px-4 py-3 text-sm text-muted-foreground">
                Every ready document has been cited at least once.
              </p>
            ) : (
              <ul className="divide-y divide-border rounded-lg border border-border bg-background">
                {data.unused_documents.map((doc) => (
                  <li key={doc.id} className="flex items-center gap-3 px-4 py-3">
                    <FileIcon
                      type={doc.file_type}
                      className="h-4 w-4 shrink-0 text-muted-foreground"
                    />
                    <Link
                      href={`/chat?document_id=${encodeURIComponent(doc.id)}`}
                      className="min-w-0 flex-1 truncate text-sm font-medium hover:underline"
                    >
                      {doc.name}
                    </Link>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      Uploaded {new Date(doc.created_at).toLocaleDateString()}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

function InsightsSkeleton() {
  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="space-y-2 rounded-lg border border-border bg-background px-4 py-3"
          >
            <Skeleton className="h-2.5 w-16" />
            <Skeleton className="h-7 w-14" />
          </div>
        ))}
      </div>
      {[0, 1].map((section) => (
        <section key={section}>
          <Skeleton className="mb-3 h-3.5 w-28" />
          <ol className="divide-y divide-border rounded-lg border border-border bg-background">
            {Array.from({ length: 5 }).map((_, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-3">
                <Skeleton className="h-3 w-4" />
                <Skeleton className="h-4 w-4 rounded" />
                <Skeleton
                  className="h-3.5 flex-1"
                  style={{ maxWidth: `${50 + ((i * 9) % 30)}%` }}
                />
                <Skeleton className="h-3 w-20" />
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

function Totals({ data }: { data: InsightsResponse["totals"] }) {
  const items = [
    { label: "Documents", value: data.documents },
    { label: "Ready", value: data.ready_documents },
    { label: "Cited", value: data.cited_documents },
    { label: "Total citations", value: data.citations },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {items.map((it) => (
        <div
          key={it.label}
          className="rounded-lg border border-border bg-background px-4 py-3"
        >
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {it.label}
          </p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {it.value.toLocaleString()}
          </p>
        </div>
      ))}
    </div>
  );
}
