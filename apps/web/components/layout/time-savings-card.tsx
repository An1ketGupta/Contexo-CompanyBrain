"use client";

import { Clock } from "lucide-react";
import { useMyTimeSavings } from "@/hooks/use-time-savings";

/** V5 #73 — Sidebar ROI card: "X hours saved this month".
 *
 *  Hidden until the user has accrued at least one assistant turn, because
 *  rendering "0h saved" before they've done anything is anti-motivating
 *  for new accounts. */
export function TimeSavingsCard() {
  const { savings, loading } = useMyTimeSavings();
  if (loading) return null;
  if (!savings) return null;
  if (savings.this_month === 0 && savings.all_time === 0) return null;

  const monthHours = Math.round((savings.this_month / 60) * 10) / 10;
  const weekHours = Math.round((savings.this_week / 60) * 10) / 10;

  return (
    <div className="mx-3 mb-2 rounded-lg border border-border bg-gradient-to-br from-indigo-50 via-background to-purple-50 p-2.5 dark:from-indigo-950/40 dark:via-background dark:to-purple-950/40">
      <div className="flex items-start gap-2">
        <div className="rounded-md bg-indigo-100 p-1.5 dark:bg-indigo-900/40">
          <Clock className="h-3.5 w-3.5 text-indigo-600 dark:text-indigo-400" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            Time saved
          </div>
          <div className="mt-0.5 flex items-baseline gap-1.5">
            <span className="text-base font-semibold text-foreground tabular-nums">
              {monthHours}h
            </span>
            <span className="text-[11px] text-muted-foreground">this month</span>
          </div>
          {weekHours > 0 && (
            <div className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">
              {weekHours}h this week
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
