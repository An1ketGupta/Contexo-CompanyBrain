"use client";

import Link from "next/link";
import { cn } from "@/lib/utils";
import { useUsage } from "@/hooks/use-usage";

const WARN_THRESHOLD = 0.8;

function formatReset(reset_at: string): string {
  const d = new Date(reset_at);
  if (Number.isNaN(d.getTime())) return "soon";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function QuotaMeter() {
  const { usage } = useUsage();
  if (!usage) return null;

  if (usage.unlimited) {
    return (
      <div className="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
        <div className="flex items-center justify-between">
          <span>Unlimited queries</span>
          <span className="uppercase tracking-wide">{usage.plan}</span>
        </div>
      </div>
    );
  }

  const limit = Math.max(usage.limit ?? 1, 1);
  const pct = Math.min((usage.used / limit) * 100, 100);
  const warn = usage.used / limit >= WARN_THRESHOLD;
  const exceeded = usage.used >= limit;

  return (
    <div className="border-t border-border px-3 py-2">
      <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          <span className="tabular-nums text-foreground">
            {usage.used.toLocaleString()}
          </span>
          {" / "}
          <span className="tabular-nums">{limit.toLocaleString()}</span>{" "}
          queries
        </span>
        <span>resets {formatReset(usage.reset_at)}</span>
      </div>
      <div
        className="h-1 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label={`Quota usage: ${Math.round(pct)}%`}
      >
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            exceeded
              ? "bg-destructive"
              : warn
                ? "bg-amber-500"
                : "bg-primary",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      {(warn || exceeded) && (
        <Link
          href="/settings"
          className={cn(
            "mt-1 inline-block text-[11px] font-medium",
            exceeded ? "text-destructive" : "text-amber-600 dark:text-amber-400",
          )}
        >
          {exceeded ? "Quota reached — upgrade →" : "Running low — upgrade →"}
        </Link>
      )}
    </div>
  );
}
