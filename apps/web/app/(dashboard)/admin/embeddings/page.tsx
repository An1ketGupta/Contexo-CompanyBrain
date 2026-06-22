"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Brain, CheckCircle2, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface FineTuneJob {
  id: string;
  status:
    | "pending"
    | "collecting_data"
    | "training"
    | "evaluating"
    | "deployed"
    | "failed"
    | "reembedding";
  base_model: string;
  fine_tuned_model_id: string | null;
  training_pairs_count: number;
  eval_score_before: number | null;
  eval_score_after: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

interface EmbeddingStatusResponse {
  plan: string;
  is_eligible: boolean;
  backend_configured: boolean;
  training_pairs: number;
  min_pairs: number;
  recommended_pairs: number;
  current_model: string | null;
  fine_tuned_at: string | null;
  eval_improvement: number | null;
  last_job: FineTuneJob | null;
  active_job: FineTuneJob | null;
}

const fetcher = async (url: string): Promise<EmbeddingStatusResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const STATUS_LABEL: Record<FineTuneJob["status"], string> = {
  pending: "Queued",
  collecting_data: "Collecting training data",
  training: "Training on GPU",
  evaluating: "Evaluating",
  deployed: "Deployed",
  failed: "Failed",
  reembedding: "Re-embedding documents",
};

export default function EmbeddingsAdminPage() {
  const { data, error, isLoading, mutate } = useSWR<EmbeddingStatusResponse>(
    "/api/admin/embeddings/status",
    fetcher,
    { revalidateOnFocus: false, refreshInterval: 15_000 },
  );

  const [starting, setStarting] = useState(false);

  const trainingPairs = data?.training_pairs ?? 0;
  const recommended = data?.recommended_pairs ?? 200;
  const minPairs = data?.min_pairs ?? 50;
  const pct = Math.min(100, Math.round((trainingPairs / recommended) * 100));

  const eligible = data?.is_eligible;
  const canStart =
    !!data &&
    eligible &&
    data.backend_configured &&
    trainingPairs >= minPairs &&
    !data.active_job;

  async function startFineTune() {
    if (!canStart) return;
    setStarting(true);
    try {
      const res = await fetch("/api/admin/embeddings/fine-tune", { method: "POST" });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Failed (${res.status})`);
      }
      toast.success("Fine-tune started — this may take 30 minutes to 4 hours");
      await mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not start fine-tune");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header>
        <div className="flex items-center gap-2">
          <Brain size={18} className="text-muted-foreground" />
          <h1 className="text-xl font-semibold tracking-tight">Custom embedding model</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Fine-tune the retrieval model on your organization&apos;s vocabulary for
          dramatically better search accuracy on your domain.
        </p>
      </header>

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          {error.message}
        </div>
      )}

      {/* Plan gate */}
      {!isLoading && data && !eligible && (
        <div className="rounded-xl border border-border bg-muted/30 p-6 text-center">
          <Badge variant="default" className="mb-3">
            {data.plan} plan
          </Badge>
          <p className="text-sm font-medium">Custom embedding models require Business</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Fine-tuning replaces the default retrieval model with one trained on your
            company&apos;s queries + cited documents. Available on Business and
            Enterprise.
          </p>
          <Link
            href="/settings"
            className="mt-3 inline-block text-sm font-medium text-primary hover:underline"
          >
            Upgrade plan →
          </Link>
        </div>
      )}

      {/* Training data progress */}
      <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Training data collected</h2>
          <Badge variant="outline">
            {trainingPairs.toLocaleString()} / {recommended.toLocaleString()} recommended
          </Badge>
        </div>
        {isLoading ? (
          <Skeleton className="h-2 w-full" />
        ) : (
          <>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  trainingPairs >= recommended ? "bg-emerald-500" : "bg-primary",
                )}
                style={{ width: `${pct}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Training pairs accumulate automatically when users copy AI responses or
              rate them positively. More usage = better fine-tuning data.
            </p>
            {trainingPairs < minPairs && (
              <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                Need at least {minPairs} pairs before you can start a fine-tune.
              </p>
            )}
          </>
        )}
      </section>

      {/* Current model */}
      <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold">Embedding model</h2>
        {isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : (
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Current</span>
              <span className="font-mono text-xs">
                {data?.current_model ?? "base (default)"}
              </span>
            </div>
            {data?.fine_tuned_at && (
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Fine-tuned</span>
                <span className="text-xs">
                  {new Date(data.fine_tuned_at).toLocaleDateString()}
                </span>
              </div>
            )}
            {data?.eval_improvement !== null && data?.eval_improvement !== undefined && (
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Retrieval accuracy delta</span>
                <Badge
                  variant={data.eval_improvement > 0 ? "default" : "outline"}
                  className={cn(
                    data.eval_improvement > 0 &&
                      "border-emerald-500/40 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300",
                  )}
                >
                  {data.eval_improvement > 0 ? "+" : ""}
                  {Math.round(data.eval_improvement * 100)}% hit@5
                </Badge>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Active / last job */}
      {data?.active_job && (
        <section className="rounded-xl border border-amber-300 bg-amber-50 p-5 dark:border-amber-800 dark:bg-amber-950/30">
          <div className="flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin text-amber-700 dark:text-amber-400" />
            <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
              Fine-tune in progress
            </p>
          </div>
          <p className="mt-1 text-xs text-amber-800 dark:text-amber-300">
            {STATUS_LABEL[data.active_job.status]} ·{" "}
            {data.active_job.training_pairs_count} training pairs
          </p>
        </section>
      )}

      {data?.last_job && data.last_job.status === "failed" && !data.active_job && (
        <section className="rounded-xl border border-destructive/40 bg-destructive/5 p-5">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            <p className="text-sm font-medium text-destructive">Last fine-tune failed</p>
          </div>
          {data.last_job.error_message && (
            <p className="mt-1 text-xs text-destructive/80">{data.last_job.error_message}</p>
          )}
        </section>
      )}

      {data?.last_job && data.last_job.status === "deployed" && !data.active_job && (
        <section className="rounded-xl border border-emerald-300 bg-emerald-50 p-5 dark:border-emerald-800 dark:bg-emerald-950/30">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-emerald-700 dark:text-emerald-400" />
            <p className="text-sm font-medium text-emerald-900 dark:text-emerald-200">
              Fine-tuned model deployed
            </p>
          </div>
          {data.last_job.eval_score_before !== null &&
            data.last_job.eval_score_after !== null && (
              <p className="mt-1 text-xs text-emerald-800 dark:text-emerald-300">
                Hit@5: {Math.round(data.last_job.eval_score_before * 100)}% →{" "}
                {Math.round(data.last_job.eval_score_after * 100)}%
              </p>
            )}
        </section>
      )}

      {/* Action button */}
      {eligible && (
        <Button
          size="lg"
          className="w-full"
          onClick={startFineTune}
          disabled={!canStart || starting}
        >
          {starting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {!data?.backend_configured ? (
            "Fine-tuning backend not configured"
          ) : data?.active_job ? (
            `Fine-tuning in progress (${STATUS_LABEL[data.active_job.status]})…`
          ) : data?.fine_tuned_at ? (
            <>
              <Sparkles className="mr-2 h-4 w-4" /> Retrain with newer data
            </>
          ) : (
            <>
              <Sparkles className="mr-2 h-4 w-4" /> Start fine-tuning
            </>
          )}
        </Button>
      )}

      {eligible && !data?.backend_configured && (
        <p className="text-center text-xs text-muted-foreground">
          The Modal.com fine-tune backend is not configured for this deployment. Set
          MODAL_FINETUNE_ENDPOINT + MODAL_FINETUNE_TOKEN in the FastAPI env.
        </p>
      )}
    </div>
  );
}
