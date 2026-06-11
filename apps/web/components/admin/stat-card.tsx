import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  /**
   * 0..1 — when present renders a thin progress bar below the value. Used for
   * "% of total" stats (active users / docs accessed).
   */
  progress?: number | null;
  className?: string;
}

/**
 * Standalone replacement for Tremor's <Card><Metric>. Keeps Tailwind v4 happy
 * and matches the rest of the dashboard's shadcn/ui surface.
 */
export function StatCard({
  label,
  value,
  hint,
  progress,
  className,
}: StatCardProps) {
  const pct =
    typeof progress === "number"
      ? Math.max(0, Math.min(100, Math.round(progress * 100)))
      : null;

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card p-4 shadow-sm",
        className,
      )}
    >
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {hint ? (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      ) : null}
      {pct !== null ? (
        <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}
