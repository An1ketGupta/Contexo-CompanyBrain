"use client";

import { CircleCheck, CircleDashed, CircleHelp } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MessageConfidence } from "@/lib/types";

interface ConfidenceBadgeProps {
  confidence: MessageConfidence;
  className?: string;
}

const COPY: Record<MessageConfidence["level"], string> = {
  high: "high confidence",
  medium: "medium confidence",
  low: "low confidence",
};

const STYLES: Record<MessageConfidence["level"], string> = {
  high: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/60 dark:text-emerald-300",
  medium:
    "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/60 dark:bg-amber-950/60 dark:text-amber-300",
  low: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/60 dark:text-rose-300",
};

export function ConfidenceBadge({ confidence, className }: ConfidenceBadgeProps) {
  const Icon =
    confidence.level === "high"
      ? CircleCheck
      : confidence.level === "medium"
        ? CircleDashed
        : CircleHelp;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        STYLES[confidence.level],
        className,
      )}
      title={`Averaged across ${confidence.n} cited chunk${confidence.n === 1 ? "" : "s"}`}
    >
      <Icon className="h-3 w-3" />
      {COPY[confidence.level]}
      <span className="tabular-nums opacity-70">{confidence.score.toFixed(1)}/10</span>
    </span>
  );
}
