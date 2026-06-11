"use client";

import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, FileText, Loader2 } from "lucide-react";

import { StatCard } from "@/components/admin/stat-card";
import { HealthBadge, type HealthLabel } from "@/components/documents/health-badge";
import { formatDistanceToNow } from "@/lib/date";
import type { DocumentFileType } from "@/lib/types";

interface AtRiskDoc {
  id: string;
  name: string;
  file_type: DocumentFileType | null;
  health_score: number;
  health_label: HealthLabel;
  last_accessed_at: string | null;
  age_days: number;
  citation_count: number;
  gap_flag_count: number;
}

interface KnowledgeHealthResponse {
  counts: Record<HealthLabel | "unscored", number>;
  total: number;
  at_risk_docs: AtRiskDoc[];
}

const fetcher = async (url: string): Promise<KnowledgeHealthResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const LABEL_ORDER: (HealthLabel | "unscored")[] = [
  "healthy",
  "stale",
  "at_risk",
  "unused",
];

const LABEL_TITLES: Record<HealthLabel | "unscored", string> = {
  healthy: "Healthy",
  stale: "Stale",
  at_risk: "At risk",
  unused: "Unused",
  unscored: "Unscored",
};

export default function KnowledgeHealthPage() {
  const { data, error, isLoading } = useSWR<KnowledgeHealthResponse>(
    "/api/admin/knowledge-health",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">
          Knowledge base health
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Which documents the AI is actually drawing on — and which look stale.
          Recomputed nightly.
        </p>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : (
        <KnowledgeHealthBody data={data} />
      )}
    </div>
  );
}

function KnowledgeHealthBody({ data }: { data: KnowledgeHealthResponse }) {
  const attentionCount =
    (data.counts.at_risk ?? 0) + (data.counts.unused ?? 0);

  return (
    <>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {LABEL_ORDER.map((label) => (
          <StatCard
            key={label}
            label={LABEL_TITLES[label]}
            value={data.counts[label] ?? 0}
            hint="documents"
          />
        ))}
      </div>

      {attentionCount > 0 ? (
        <section className="space-y-2">
          <p className="text-sm font-medium text-orange-700 dark:text-orange-400">
            <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />
            {attentionCount} document{attentionCount === 1 ? "" : "s"} need
            attention
          </p>
          <div className="space-y-2">
            {data.at_risk_docs.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
              >
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{doc.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {doc.last_accessed_at
                      ? `Last accessed ${formatDistanceToNow(doc.last_accessed_at)}`
                      : "Never accessed"}{" "}
                    · {doc.age_days} days old · {doc.citation_count} citations
                    {doc.gap_flag_count > 0
                      ? ` · ${doc.gap_flag_count} gap flag${doc.gap_flag_count === 1 ? "" : "s"}`
                      : ""}
                  </p>
                </div>
                <HealthBadge
                  label={doc.health_label}
                  score={doc.health_score}
                />
                <Link
                  href={`/documents?id=${encodeURIComponent(doc.id)}`}
                  className="shrink-0 text-xs font-medium text-primary hover:underline"
                >
                  Review →
                </Link>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <p className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          Every document looks healthy. Nothing to review.
        </p>
      )}
    </>
  );
}
