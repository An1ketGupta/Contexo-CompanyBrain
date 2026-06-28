"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import useSWR from "swr";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface ConfidenceThresholdsResponse {
  high: number;
  medium: number;
  block: number;
  defaults: { high: number; medium: number; block: number };
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function ConfidenceSettingsPage() {
  const { data, error, isLoading, mutate } = useSWR<ConfidenceThresholdsResponse>(
    "/api/admin/config/confidence-thresholds",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">
          Confidence thresholds
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Tune when the chat confidence badge shows green / amber / red, and
          set an optional block tier below which AI-generated content cannot
          be posted to Slack, sent as email, or exported to Notion / Google
          Docs. Scores are the average vector-similarity (0–10) of the chunks
          cited in the answer.
        </p>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <div className="space-y-6 rounded-xl border border-border bg-card p-6">
          {[0, 1, 2].map((i) => (
            <div key={i} className="space-y-3">
              <div className="flex items-baseline justify-between">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-10" />
              </div>
              <Skeleton className="h-2 w-full rounded-full" />
              <Skeleton className="h-2.5 w-3/4" />
            </div>
          ))}
          <div className="flex justify-end gap-2 pt-2">
            <Skeleton className="h-9 w-24 rounded-md" />
            <Skeleton className="h-9 w-28 rounded-md" />
          </div>
        </div>
      ) : (
        <ThresholdEditor initial={data} onSaved={() => mutate()} />
      )}
    </div>
  );
}

function ThresholdEditor({
  initial,
  onSaved,
}: {
  initial: ConfidenceThresholdsResponse;
  onSaved: () => void;
}) {
  // The wire format is raw cosine [0, 1]. The slider is 0–100 (integer)
  // for a clean UX. We convert at the boundaries only.
  const [high, setHigh] = useState(Math.round(initial.high * 100));
  const [medium, setMedium] = useState(Math.round(initial.medium * 100));
  const [block, setBlock] = useState(Math.round(initial.block * 100));
  const [saving, setSaving] = useState(false);

  // Maintain the well-ordering invariant: 0 <= block <= medium <= high.
  // Clamping on input is gentler than throwing on submit.
  useEffect(() => {
    if (medium > high) setMedium(high);
  }, [high, medium]);

  useEffect(() => {
    if (block > medium) setBlock(medium);
  }, [medium, block]);

  const dirty =
    Math.round(initial.high * 100) !== high ||
    Math.round(initial.medium * 100) !== medium ||
    Math.round(initial.block * 100) !== block;

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/admin/config/confidence-thresholds", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          high: high / 100,
          medium: medium / 100,
          block: block / 100,
        }),
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        toast.error(payload.detail ?? "Failed to save.");
        return;
      }
      toast.success("Thresholds updated.");
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  const resetToDefaults = () => {
    setHigh(Math.round(initial.defaults.high * 100));
    setMedium(Math.round(initial.defaults.medium * 100));
    setBlock(Math.round(initial.defaults.block * 100));
  };

  return (
    <div className="space-y-6">
      <section className="space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm">
        <SliderRow
          label="High confidence (green)"
          description={`Average score ≥ ${(high / 10).toFixed(1)}/10 shows green.`}
          value={high}
          min={medium}
          onChange={setHigh}
        />
        <SliderRow
          label="Medium confidence (amber)"
          description={`Average score ≥ ${(medium / 10).toFixed(1)}/10 shows amber. Below shows red.`}
          value={medium}
          min={block}
          max={high}
          onChange={setMedium}
        />

        <div className="border-t pt-4">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Preview
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <PreviewBadge level="high" score={(high / 10).toFixed(1)} />
            <PreviewBadge
              level="medium"
              score={(((high + medium) / 2) / 10).toFixed(1)}
            />
            <PreviewBadge level="low" score={((medium / 10) * 0.7).toFixed(1)} />
          </div>
        </div>
      </section>

      <section className="space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div>
            <h2 className="text-sm font-medium">Outbound write block</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Below this confidence score, AI-generated content cannot be
              Published to external destinations (Slack, Gmail, Notion, Google
              Docs). Set to 0 to disable. The badge stays the same — only the
              publish actions are gated.
            </p>
          </div>
        </div>
        <SliderRow
          label="Block threshold"
          description={
            block === 0
              ? "Off — any confidence level is allowed to publish."
              : `Below ${(block / 10).toFixed(1)}/10, publish buttons are disabled.`
          }
          value={block}
          min={0}
          max={medium}
          onChange={setBlock}
        />
      </section>

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={resetToDefaults}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          Reset to defaults (
          {(initial.defaults.high * 10).toFixed(1)} /{" "}
          {(initial.defaults.medium * 10).toFixed(1)} /{" "}
          {(initial.defaults.block * 10).toFixed(1)})
        </button>
        <Button onClick={save} disabled={!dirty || saving}>
          {saving ? (
            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="mr-2 h-3.5 w-3.5" />
          )}
          Save thresholds
        </Button>
      </div>
    </div>
  );
}

function SliderRow({
  label,
  description,
  value,
  min,
  max = 100,
  onChange,
}: {
  label: string;
  description: string;
  value: number;
  min: number;
  max?: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <label className="text-sm font-medium">{label}</label>
        <span className="font-mono text-xs text-muted-foreground tabular-nums">
          {(value / 10).toFixed(1)}/10
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  );
}

function PreviewBadge({
  level,
  score,
}: {
  level: "high" | "medium" | "low";
  score: string;
}) {
  const styles = {
    high: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    medium: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
    low: "bg-red-500/10 text-red-700 dark:text-red-300",
  } as const;
  const labels = { high: "High", medium: "Medium", low: "Low" } as const;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium ${styles[level]}`}
    >
      <span className="font-mono tabular-nums">{score}/10</span>
      <span>·</span>
      <span>{labels[level]} confidence</span>
    </span>
  );
}
