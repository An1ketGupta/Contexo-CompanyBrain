"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Target,
  Users,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type ResolutionStatus = "open" | "in_progress" | "resolved" | "dismissed";

interface Cluster {
  id: string;
  report_id: string;
  topic: string;
  suggested_doc_title: string | null;
  suggested_outline: string | null;
  query_count: number;
  unique_users: number;
  avg_confidence: number | null;
  priority_score: number;
  sample_queries: string[];
  gap_ids: string[];
  resolution_status: ResolutionStatus;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_doc_id: string | null;
  created_at: string;
}

interface ReportDetail {
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
  clusters: Cluster[];
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function ReportDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id;

  const { data, isLoading, mutate } = useSWR<ReportDetail>(
    id ? `/api/admin/knowledge-gap-reports/${id}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        latest?.status === "generating" ? 4000 : 0,
    },
  );

  if (isLoading) {
    return (
      <div className="container max-w-5xl mx-auto py-8 px-4">
        <Skeleton className="h-8 w-48 mb-4" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (!data) {
    return (
      <div className="container max-w-5xl mx-auto py-8 px-4">
        <p className="text-sm text-muted-foreground">Report not found.</p>
      </div>
    );
  }

  const startDate = new Date(data.period_start);
  const endDate = new Date(data.period_end);

  return (
    <div className="container max-w-5xl mx-auto py-8 px-4">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => router.push("/admin/knowledge-gaps/reports")}
        className="mb-3"
      >
        <ArrowLeft className="w-4 h-4 mr-2" />
        All reports
      </Button>

      <div className="rounded-2xl border border-border bg-card p-5 mb-6">
        <h1 className="text-2xl font-extrabold tracking-tight mb-1">
          Week of {startDate.toLocaleDateString()} – {endDate.toLocaleDateString()}
        </h1>
        <p className="text-sm text-muted-foreground mb-4">
          {data.total_clusters} clusters from {data.total_gaps} queries.{" "}
          {data.status === "generating"
            ? "Generation in progress — auto-refreshing."
            : `Generated ${new Date(data.created_at).toLocaleString()}.`}
        </p>
        {data.error_message ? (
          <p className="text-sm text-destructive">{data.error_message}</p>
        ) : null}
      </div>

      {data.status === "generating" ? (
        <GeneratingState />
      ) : data.clusters.length === 0 ? (
        <p className="text-sm text-muted-foreground p-8 text-center border border-dashed border-border bg-card rounded-2xl">
          No clusters above the priority threshold this week. 🎉
        </p>
      ) : (
        <div className="space-y-3">
          {data.clusters.map((c) => (
            <ClusterCard
              key={c.id}
              cluster={c}
              onResolve={async (status) => {
                try {
                  const res = await fetch(
                    `/api/admin/knowledge-gap-clusters/${c.id}`,
                    {
                      method: "PATCH",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ resolution_status: status }),
                    },
                  );
                  if (!res.ok) throw new Error(`Failed (${res.status})`);
                  toast.success(`Marked ${status.replace("_", " ")}.`);
                  await mutate();
                } catch (e) {
                  toast.error(
                    e instanceof Error ? e.message : "Update failed.",
                  );
                }
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function GeneratingState() {
  return (
    <div className="rounded-2xl border border-border bg-card p-10 text-center">
      <Loader2 className="w-10 h-10 mx-auto text-brand animate-spin mb-3" />
      <p className="text-sm text-muted-foreground">
        Clustering queries → LLM-naming topics → drafting suggested doc outlines.
      </p>
    </div>
  );
}

function ClusterCard({
  cluster,
  onResolve,
}: {
  cluster: Cluster;
  onResolve: (status: ResolutionStatus) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [acting, setActing] = useState(false);

  const priority = (cluster.priority_score * 100).toFixed(0);
  const isOpen = cluster.resolution_status === "open";
  const isResolved =
    cluster.resolution_status === "resolved" ||
    cluster.resolution_status === "dismissed";

  return (
    <div
      className={`rounded-2xl border border-border bg-card overflow-hidden ${
        isResolved ? "opacity-60" : ""
      }`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left p-4 flex items-center gap-4 hover:bg-muted/50 transition-colors"
      >
        <PriorityBadge score={cluster.priority_score} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold truncate">{cluster.topic}</h3>
            <StatusBadge status={cluster.resolution_status} />
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground mt-1">
            <span>
              <strong className="text-foreground">{cluster.query_count}</strong>{" "}
              queries
            </span>
            <span className="flex items-center gap-1">
              <Users className="w-3 h-3" />
              {cluster.unique_users} users
            </span>
            {cluster.avg_confidence !== null ? (
              <span>
                avg conf <strong className="text-foreground">{cluster.avg_confidence.toFixed(1)}</strong>/10
              </span>
            ) : null}
            <span>priority {priority}</span>
          </div>
        </div>
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="w-4 h-4 text-muted-foreground" />
        )}
      </button>
      {expanded ? (
        <div className="border-t p-4 space-y-4">
          {cluster.suggested_doc_title ? (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">
                Suggested doc
              </p>
              <p className="text-sm font-semibold">{cluster.suggested_doc_title}</p>
            </div>
          ) : null}
          {cluster.suggested_outline ? (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">
                Suggested outline
              </p>
              <pre className="text-xs whitespace-pre-wrap font-sans bg-muted/40 rounded p-3 border">
                {cluster.suggested_outline}
              </pre>
            </div>
          ) : null}
          {cluster.sample_queries.length ? (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">
                Sample queries
              </p>
              <ul className="text-xs space-y-1 list-disc list-inside">
                {cluster.sample_queries.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="flex gap-2 pt-2">
            {isOpen ? (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={acting}
                  onClick={async () => {
                    setActing(true);
                    await onResolve("in_progress");
                    setActing(false);
                  }}
                >
                  <Target className="w-3 h-3 mr-1" />
                  Mark in progress
                </Button>
                <Button
                  size="sm"
                  disabled={acting}
                  onClick={async () => {
                    setActing(true);
                    await onResolve("resolved");
                    setActing(false);
                  }}
                >
                  <CheckCircle2 className="w-3 h-3 mr-1" />
                  Mark resolved
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={acting}
                  onClick={async () => {
                    setActing(true);
                    await onResolve("dismissed");
                    setActing(false);
                  }}
                >
                  <X className="w-3 h-3 mr-1" />
                  Dismiss
                </Button>
              </>
            ) : (
              <Button
                size="sm"
                variant="outline"
                disabled={acting}
                onClick={async () => {
                  setActing(true);
                  await onResolve("open");
                  setActing(false);
                }}
              >
                Reopen
              </Button>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PriorityBadge({ score }: { score: number }) {
  const v = Math.round(score * 100);
  const tone =
    v >= 60
      ? "bg-destructive-soft text-destructive-ink"
      : v >= 35
        ? "bg-amber-tint text-amber-ink"
        : "bg-muted text-muted-foreground";
  return (
    <div
      className={`shrink-0 w-12 h-12 rounded-xl flex items-center justify-center text-base font-extrabold tabular-nums ${tone}`}
    >
      {v}
    </div>
  );
}

function StatusBadge({ status }: { status: ResolutionStatus }) {
  const styles: Record<ResolutionStatus, string> = {
    open: "bg-muted text-muted-foreground",
    in_progress: "bg-brand-tint text-brand",
    resolved: "bg-success-tint text-success-ink",
    dismissed: "bg-muted text-muted-foreground",
  };
  return (
    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${styles[status]}`}>
      {status.replace("_", " ")}
    </span>
  );
}
