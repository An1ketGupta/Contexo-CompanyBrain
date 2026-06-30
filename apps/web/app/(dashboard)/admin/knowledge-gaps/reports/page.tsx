"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  AlertCircle,
  ArrowRight,
  Calendar,
  ChevronRight,
  Loader2,
  RefreshCw,
  TrendingUp,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface ReportSummary {
  id: string;
  period_start: string;
  period_end: string;
  total_gaps: number;
  total_clusters: number;
  zero_hit_count: number;
  low_conf_count: number;
  status: "generating" | "ready" | "failed";
  error_message: string | null;
  created_at: string;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function KnowledgeGapReportsPage() {
  const router = useRouter();
  const { data, isLoading, mutate } = useSWR<ReportSummary[]>(
    "/api/admin/knowledge-gap-reports?limit=24",
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 6000 },
  );

  const [generating, setGenerating] = useState(false);

  const generate = async () => {
    setGenerating(true);
    try {
      const res = await fetch("/api/admin/knowledge-gap-reports/generate", {
        method: "POST",
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `Failed (${res.status})`);
      }
      toast.success("Generating a new report — this takes 30–60s.");
      await mutate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to start.");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="container max-w-5xl mx-auto py-8 px-4">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <TrendingUp className="w-6 h-6" />
            Knowledge gap reports
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Weekly platform-health digest. Clusters low-confidence and zero-hit
            queries by topic, prioritized by frequency, recency, user-spread,
            and confidence delta.
          </p>
        </div>
        <Button onClick={generate} disabled={generating}>
          {generating ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4 mr-2" />
          )}
          Generate now
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : !data?.length ? (
        <EmptyState onGenerate={generate} generating={generating} />
      ) : (
        <div className="space-y-3">
          {data.map((r) => (
            <ReportCard
              key={r.id}
              report={r}
              onOpen={() =>
                router.push(`/admin/knowledge-gaps/reports/${r.id}`)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ReportCard({
  report,
  onOpen,
}: {
  report: ReportSummary;
  onOpen: () => void;
}) {
  const start = new Date(report.period_start);
  const end = new Date(report.period_end);

  return (
    <button
      onClick={onOpen}
      className="w-full text-left rounded-lg border bg-card hover:bg-accent/40 transition-colors p-5 flex items-center gap-4"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <Calendar className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {start.toLocaleDateString()} – {end.toLocaleDateString()}
          </span>
          <StatusPill status={report.status} />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
          <span>
            <strong className="text-foreground">{report.total_clusters}</strong>{" "}
            clusters
          </span>
          <span>
            <strong className="text-foreground">{report.total_gaps}</strong> queries
          </span>
          <span>
            Generated {new Date(report.created_at).toLocaleString()}
          </span>
        </div>
        {report.error_message ? (
          <p className="text-xs text-destructive mt-1">{report.error_message}</p>
        ) : null}
      </div>
      <ChevronRight className="w-5 h-5 text-muted-foreground" />
    </button>
  );
}

function StatusPill({ status }: { status: ReportSummary["status"] }) {
  const styles: Record<ReportSummary["status"], string> = {
    generating: "bg-blue-100 text-blue-700",
    ready: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${styles[status]}`}>
      {status}
    </span>
  );
}

function EmptyState({
  onGenerate,
  generating,
}: {
  onGenerate: () => void;
  generating: boolean;
}) {
  return (
    <div className="rounded-lg border border-dashed p-12 text-center">
      <AlertCircle className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
      <h2 className="text-lg font-semibold mb-2">No reports yet</h2>
      <p className="text-sm text-muted-foreground mb-6 max-w-md mx-auto">
        Reports auto-generate every Monday at 09:00 UTC. You can also kick one
        off now to cluster the last 7 days of gap signals into a prioritized
        list of missing docs.
      </p>
      <Button onClick={onGenerate} disabled={generating}>
        {generating ? (
          <Loader2 className="w-4 h-4 mr-2 animate-spin" />
        ) : (
          <ArrowRight className="w-4 h-4 mr-2" />
        )}
        Generate first report
      </Button>
    </div>
  );
}
