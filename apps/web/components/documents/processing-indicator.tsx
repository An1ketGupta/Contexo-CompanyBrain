"use client";

import { useEffect, useState } from "react";

interface ProcessingIndicatorProps {
  /** ISO timestamp of when ingestion started (status = processing). */
  startedAt: string;
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

/**
 * Animated indeterminate bar + honest elapsed-time counter.
 *
 * We intentionally don't show a percentage: the backend doesn't emit progress
 * events, so any % would be a lie. Realtime flips the row to `ready` / `failed`
 * the moment ingestion finishes — this indicator just signals "still working".
 */
export function ProcessingIndicator({ startedAt }: ProcessingIndicatorProps) {
  const [elapsed, setElapsed] = useState(() => secondsSince(startedAt));

  useEffect(() => {
    setElapsed(secondsSince(startedAt));
    const id = setInterval(() => setElapsed(secondsSince(startedAt)), 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <div className="flex min-w-[7.5rem] items-center gap-2">
      <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className="absolute inset-y-0 w-1/3 rounded-full bg-primary"
          style={{
            animation: "progress-indeterminate 1.6s ease-in-out infinite",
          }}
        />
      </div>
      <span
        className="shrink-0 text-[11px] tabular-nums text-muted-foreground"
        aria-label={`Processing for ${formatElapsed(elapsed)}`}
      >
        {formatElapsed(elapsed)}
      </span>
    </div>
  );
}

function secondsSince(iso: string): number {
  const start = new Date(iso).getTime();
  if (Number.isNaN(start)) return 0;
  return Math.max(0, Math.floor((Date.now() - start) / 1000));
}
