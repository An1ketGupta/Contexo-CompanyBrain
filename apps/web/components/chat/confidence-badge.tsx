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
  high: "border-transparent bg-success-tint text-success",
  medium: "border-transparent bg-amber-tint text-amber",
  low: "border-transparent bg-destructive-soft text-destructive",
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
