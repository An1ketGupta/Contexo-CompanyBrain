"use client";

import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Brain, CheckCircle2, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
      <header className="mb-1">
        <div className="flex items-center gap-2 text-brand">
          <Brain className="h-4 w-4" />
          <span className="text-[13px] font-bold text-brand">Retrieval</span>
        </div>
        <h1 className="mt-1 text-3xl font-extrabold tracking-tight">Custom embedding model</h1>
        <p className="mt-1 max-w-[64ch] text-[15px] leading-relaxed text-muted-foreground">
          Fine-tune the retrieval model on your organization&apos;s vocabulary for
          dramatically better search accuracy on your domain.
        </p>
      </header>

      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive-soft p-4">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive-ink" />
          <p className="text-sm text-body">{error.message}</p>
        </div>
      )}

      {/* Plan gate */}
      {!isLoading && data && !eligible && (
        <Card className="p-6 text-center">
          <Badge variant="outline" className="mb-3">
            {data.plan} plan
          </Badge>
          <p className="text-[15px] font-bold">Custom embedding models require Business</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Fine-tuning replaces the default retrieval model with one trained on your
            company&apos;s queries + cited documents. Available on Business and
            Enterprise.
          </p>
          <Link
            href="/settings"
            className="mt-3 inline-block text-sm font-semibold text-brand hover:underline"
          >
            Upgrade plan →
          </Link>
        </Card>
      )}

      {/* Training data progress */}
      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h2 className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            Training data collected
          </h2>
          <Badge variant="outline">
            {trainingPairs.toLocaleString()} / {recommended.toLocaleString()} recommended
          </Badge>
        </div>
        {isLoading ? (
          <Skeleton className="h-2 w-full" />
        ) : (
          <>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  trainingPairs >= recommended ? "bg-success" : "bg-brand",
                )}
                style={{ width: `${pct}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Training pairs accumulate automatically when users copy AI responses or
              rate them positively. More usage = better fine-tuning data.
            </p>
            {trainingPairs < minPairs && (
              <p className="mt-2 text-xs font-bold text-amber-ink">
                Need at least {minPairs} pairs before you can start a fine-tune.
              </p>
            )}
          </>
        )}
      </Card>

      {/* Current model */}
      <Card className="p-6">
        <h2 className="mb-4 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Embedding model
        </h2>
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
                <Badge variant={data.eval_improvement > 0 ? "success" : "outline"}>
                  {data.eval_improvement > 0 ? "+" : ""}
                  {Math.round(data.eval_improvement * 100)}% hit@5
                </Badge>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Active / last job */}
      {data?.active_job && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-ink/20 bg-amber-tint p-4">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-amber-ink" />
          <div>
            <p className="text-sm font-bold text-amber-ink">Fine-tune in progress</p>
            <p className="mt-1 text-xs text-body">
              {STATUS_LABEL[data.active_job.status]} ·{" "}
              {data.active_job.training_pairs_count} training pairs
            </p>
          </div>
        </div>
      )}

      {data?.last_job && data.last_job.status === "failed" && !data.active_job && (
        <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive-soft p-4">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive-ink" />
          <div>
            <p className="text-sm font-bold text-destructive-ink">Last fine-tune failed</p>
            {data.last_job.error_message && (
              <p className="mt-1 text-xs text-body">{data.last_job.error_message}</p>
            )}
          </div>
        </div>
      )}

      {data?.last_job && data.last_job.status === "deployed" && !data.active_job && (
        <div className="flex items-start gap-3 rounded-xl border border-success-ink/20 bg-success-tint p-4">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success-ink" />
          <div>
            <p className="text-sm font-bold text-success-ink">Fine-tuned model deployed</p>
            {data.last_job.eval_score_before !== null &&
              data.last_job.eval_score_after !== null && (
                <p className="mt-1 text-xs text-body">
                  Hit@5: {Math.round(data.last_job.eval_score_before * 100)}% →{" "}
                  {Math.round(data.last_job.eval_score_after * 100)}%
                </p>
              )}
          </div>
        </div>
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
