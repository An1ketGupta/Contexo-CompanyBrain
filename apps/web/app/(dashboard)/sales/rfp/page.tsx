"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { ChevronRight, FileSpreadsheet, Loader2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PageHeader,
  Stat,
  StatGrid,
  StatusPill,
  type PillTone,
} from "@/components/actual/kit";

interface RfpRow {
  id: string;
  source_filename: string;
  status: string;
  gap_count: number;
  created_at: string;
  generated_at: string | null;
}

const fetcher = async (url: string): Promise<{ rfps: RfpRow[] }> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_TONE: Record<string, PillTone> = {
  // legacy
  extracted: "blue",
  reviewed: "amber",
  generating: "amber",
  // agent v2
  extracting: "blue",
  awaiting_requirements_review: "amber",
  drafting: "amber",
  awaiting_rep_review: "violet",
  awaiting_legal_review: "violet",
  legal_rejected: "amber",
  finalizing: "amber",
  // terminal
  ready: "green",
  failed: "red",
};

const STATUS_LABEL: Record<string, string> = {
  extracting: "Extracting",
  awaiting_requirements_review: "Review requirements",
  drafting: "Drafting",
  awaiting_rep_review: "Review answers",
  awaiting_legal_review: "Legal review",
  legal_rejected: "Re-drafting",
  finalizing: "Finalizing",
  ready: "Ready",
  failed: "Failed",
  extracted: "Extracted",
  reviewed: "Reviewed",
  generating: "Drafting",
};

const IN_REVIEW = new Set([
  "awaiting_requirements_review",
  "awaiting_rep_review",
  "awaiting_legal_review",
]);

export default function RfpListPage() {
  const router = useRouter();
  const { data, error, isLoading, mutate } = useSWR<{ rfps: RfpRow[] }>(
    "/api/sales/rfp",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const rfps = useMemo(() => data?.rfps ?? [], [data]);
  const stats = useMemo(
    () => ({
      total: rfps.length,
      ready: rfps.filter((r) => r.status === "ready").length,
      inReview: rfps.filter((r) => IN_REVIEW.has(r.status)).length,
      gaps: rfps.reduce((n, r) => n + (r.gap_count || 0), 0),
    }),
    [rfps],
  );

  const handleUpload = async (file: File) => {
    setUploadError(null);
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/sales/rfp/upload", { method: "POST", body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      const created = await res.json();
      await mutate();
      router.push(`/sales/rfp/${created.id}`);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Sales"
        title="RFP responses"
        description="Upload an RFP (XLSX, DOCX, or PDF). The agent extracts requirements, drafts answers from your KB, runs rep + legal review, then exports back into the buyer's original file."
        actions={
          <label className="cursor-pointer">
            <input
              type="file"
              accept=".xlsx,.pdf,.docx,.doc,.txt,.md,.csv"
              className="hidden"
              disabled={uploading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
              }}
            />
            <Button asChild>
              <span>
                {uploading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Upload className="size-4" />
                )}
                {uploading ? "Uploading…" : "Upload RFP"}
              </span>
            </Button>
          </label>
        }
      />

      <StatGrid>
        <Stat label="Total RFPs" value={stats.total} />
        <Stat label="Ready" value={stats.ready} tone="up" />
        <Stat label="In review" value={stats.inReview} />
        <Stat
          label="Open gaps"
          value={stats.gaps}
          tone={stats.gaps > 0 ? "down" : "flat"}
        />
      </StatGrid>

      {uploadError && (
        <div className="rounded-2xl border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive">
          {uploadError}
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-[70px] w-full rounded-2xl" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive">
          Failed to load RFPs.
        </div>
      ) : !rfps.length ? (
        <EmptyState />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          {rfps.map((r) => (
            <Link
              key={r.id}
              href={`/sales/rfp/${r.id}`}
              className="flex items-center gap-4 border-b border-border px-5 py-4 transition-colors last:border-b-0 hover:bg-muted/40"
            >
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-brand">
                <FileSpreadsheet className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-bold text-foreground">
                  {r.source_filename}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {new Date(r.created_at).toLocaleString()}
                </p>
              </div>
              {r.gap_count > 0 && (
                <StatusPill tone="amber">{r.gap_count} gaps</StatusPill>
              )}
              <StatusPill tone={STATUS_TONE[r.status] ?? "gray"}>
                {STATUS_LABEL[r.status] ?? r.status}
              </StatusPill>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-background p-12 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
        <Upload className="h-5 w-5" />
      </div>
      <p className="mt-3 text-sm font-bold">No RFPs uploaded yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Upload a buyer questionnaire and the agent takes it from extraction to a
        finished response.
      </p>
    </div>
  );
}
