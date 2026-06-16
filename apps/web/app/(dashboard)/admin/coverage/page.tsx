"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  AlertTriangle,
  Check,
  RefreshCw,
  Sparkles,
  Target,
  X,
} from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "@/lib/date";

// Mirrors the payload shape from app/services/coverage.py. Kept inline rather
// than in lib/types.ts because nothing else in the app consumes it (yet).
interface CoverageCategory {
  id: string;
  label: string;
  description: string;
  covered: boolean;
  evidence: "keyword" | "semantic" | "none";
  keyword_hits: number;
  semantic_hits: number;
  avg_similarity: number | null;
  matched_documents: string[];
  example_questions: string[];
  gap_message: string | null;
}

interface CoverageResponse {
  overall_score: number;        // 0.0 – 1.0
  covered: number;
  total: number;
  total_documents: number;
  categories: CoverageCategory[];
  gaps: CoverageCategory[];
  cached?: boolean;
  stale?: boolean;
  computed_at?: string;
}

const fetcher = async (url: string): Promise<CoverageResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function CoverageScorePage() {
  const [refreshing, setRefreshing] = useState(false);

  const { data, error, isLoading, mutate } = useSWR<CoverageResponse>(
    "/api/admin/coverage-score",
    fetcher,
    { revalidateOnFocus: false },
  );

  async function recompute() {
    setRefreshing(true);
    try {
      const res = await fetch("/api/admin/coverage-score?refresh=true");
      if (!res.ok) throw new Error(`Recompute failed (${res.status})`);
      const next = (await res.json()) as CoverageResponse;
      await mutate(next, { revalidate: false });
    } catch (err) {
      console.error("coverage recompute failed", err);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Knowledge Base Coverage
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            How completely your documents cover the eight knowledge areas every
            business needs.
          </p>
        </div>
        <button
          type="button"
          onClick={recompute}
          disabled={refreshing}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium",
            "transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60",
          )}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", refreshing && "animate-spin")}
          />
          Recompute
        </button>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <CoverageSkeleton />
      ) : (
        <CoverageBody data={data} />
      )}
    </div>
  );
}

function CoverageBody({ data }: { data: CoverageResponse }) {
  const scorePercent = Math.round(data.overall_score * 100);

  if (data.total_documents === 0) {
    return <NoDocsEmpty />;
  }

  return (
    <>
      <ScoreCard
        scorePercent={scorePercent}
        covered={data.covered}
        total={data.total}
        gaps={data.gaps.length}
        computedAt={data.computed_at}
        stale={Boolean(data.stale)}
      />

      <section>
        <h2 className="mb-3 text-sm font-semibold">Categories</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {data.categories.map((cat) => (
            <CategoryCard key={cat.id} cat={cat} />
          ))}
        </div>
      </section>

      {data.gaps.length > 0 ? <GapsPanel gaps={data.gaps} /> : null}
    </>
  );
}

function ScoreCard({
  scorePercent,
  covered,
  total,
  gaps,
  computedAt,
  stale,
}: {
  scorePercent: number;
  covered: number;
  total: number;
  gaps: number;
  computedAt: string | undefined;
  stale: boolean;
}) {
  const stroke =
    scorePercent >= 75
      ? "#10b981" // emerald
      : scorePercent >= 50
        ? "#f59e0b" // amber
        : "#ef4444"; // red

  return (
    <div className="flex flex-col items-center gap-6 rounded-xl border border-border bg-background p-6 sm:flex-row sm:items-center">
      <div className="relative h-24 w-24 shrink-0">
        <svg viewBox="0 0 36 36" className="h-24 w-24 -rotate-90">
          <circle
            cx="18"
            cy="18"
            r="15.9"
            fill="none"
            className="stroke-muted"
            strokeWidth="2.5"
          />
          <circle
            cx="18"
            cy="18"
            r="15.9"
            fill="none"
            stroke={stroke}
            strokeWidth="2.5"
            strokeDasharray={`${scorePercent} ${100 - scorePercent}`}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xl font-semibold tracking-tight">
            {scorePercent}%
          </span>
        </div>
      </div>

      <div className="text-center sm:text-left">
        <p className="text-base font-semibold">
          {covered} of {total} categories covered
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {gaps === 0
            ? "Excellent — your knowledge base covers every major category."
            : `${gaps} category gap${gaps > 1 ? "s" : ""} detected. Upload documents to fill them in.`}
        </p>
        {computedAt ? (
          <p className="mt-2 text-xs text-muted-foreground">
            {stale ? "Stale score — using last good result. " : null}
            Last computed {formatDistanceToNow(computedAt)}.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function CategoryCard({ cat }: { cat: CoverageCategory }) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-xl border p-4 transition-colors",
        cat.covered
          ? "border-emerald-200 bg-emerald-50/60 dark:border-emerald-900/60 dark:bg-emerald-950/30"
          : "border-rose-200 bg-rose-50/60 dark:border-rose-900/60 dark:bg-rose-950/30",
      )}
    >
      <div
        className={cn(
          "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
          cat.covered ? "bg-emerald-500" : "bg-rose-400",
        )}
        aria-hidden="true"
      >
        {cat.covered ? (
          <Check className="h-3.5 w-3.5 text-white" strokeWidth={3} />
        ) : (
          <X className="h-3.5 w-3.5 text-white" strokeWidth={3} />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">{cat.label}</p>
          {cat.evidence === "semantic" ? (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
              title={`Matched semantically (${cat.semantic_hits} hits, avg sim ${cat.avg_similarity ?? "—"})`}
            >
              <Sparkles className="h-2.5 w-2.5" /> semantic
            </span>
          ) : null}
        </div>

        <p className="mt-0.5 text-xs text-muted-foreground">{cat.description}</p>

        {cat.covered && cat.matched_documents.length > 0 ? (
          <div className="mt-2 space-y-0.5">
            {cat.matched_documents.slice(0, 3).map((name) => (
              <p
                key={name}
                className="truncate text-xs text-muted-foreground"
                title={name}
              >
                • {name}
              </p>
            ))}
          </div>
        ) : null}

        {!cat.covered ? (
          <>
            <p className="mt-1.5 text-xs text-rose-700 dark:text-rose-400">
              {cat.gap_message}
            </p>
            <Link
              href="/documents"
              className="mt-1 inline-block text-xs font-medium text-primary hover:underline"
            >
              Upload documents →
            </Link>
          </>
        ) : null}
      </div>
    </div>
  );
}

function GapsPanel({ gaps }: { gaps: CoverageCategory[] }) {
  const questions = gaps.flatMap((g) => g.example_questions).slice(0, 6);
  if (!questions.length) return null;

  return (
    <section className="rounded-xl border border-border bg-background p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <Target className="h-4 w-4 text-amber-500" />
        Questions your team can't answer yet
      </div>
      <ul className="space-y-1.5">
        {questions.map((q) => (
          <li
            key={q}
            className="flex items-start gap-2 text-sm text-muted-foreground"
          >
            <span className="mt-0.5">•</span>
            <span className="italic">"{q}"</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function NoDocsEmpty() {
  return (
    <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
      <p className="text-sm font-medium">No documents uploaded yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Coverage scores compare your documents against eight canonical business
        categories. Add some documents first.
      </p>
      <Link
        href="/documents"
        className="mt-4 inline-block text-sm font-medium text-primary hover:underline"
      >
        Upload your first document →
      </Link>
    </div>
  );
}

function CoverageSkeleton() {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="space-y-2 rounded-lg border border-border bg-card p-4"
          >
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-8 w-16" />
          </div>
        ))}
      </div>
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="flex items-start gap-3 rounded-lg border border-border bg-card px-4 py-3.5"
          >
            <Skeleton className="mt-0.5 h-5 w-5 shrink-0 rounded-full" />
            <div className="min-w-0 flex-1 space-y-1.5">
              <Skeleton
                className="h-3.5"
                style={{ width: `${30 + ((i * 11) % 25)}%` }}
              />
              <Skeleton className="h-2.5 w-3/4" />
            </div>
            <Skeleton className="h-7 w-20 rounded-md" />
          </div>
        ))}
      </div>
    </div>
  );
}
