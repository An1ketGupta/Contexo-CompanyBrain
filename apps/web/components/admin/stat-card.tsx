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
  /**
   * Optional delta line under the value (e.g. "+12% vs last period"). Tone
   * drives the color per Actual's up/down/flat convention.
   */
  delta?: React.ReactNode;
  deltaTone?: "up" | "down" | "flat";
  className?: string;
}

/**
 * Actual design-system stat tile. Cool-white card on the canvas, hairline
 * border, generous 16px corners. Label is a JetBrains-Mono eyebrow; the value
 * is the signature extrabold, tight-tracked number.
 */
export function StatCard({
  label,
  value,
  hint,
  progress,
  delta,
  deltaTone = "flat",
  className,
}: StatCardProps) {
  const pct =
    typeof progress === "number"
      ? Math.max(0, Math.min(100, Math.round(progress * 100)))
      : null;

  return (
    <div
      className={cn(
        "rounded-2xl border border-border bg-card p-5",
        className,
      )}
    >
      <p className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
        {label}
      </p>
      <p className="mt-2.5 text-[28px] font-extrabold leading-none tracking-tight tabular-nums">
        {value}
      </p>
      {delta ? (
        <p
          className={cn(
            "mt-2 text-xs font-bold",
            deltaTone === "up" && "text-success-ink",
            deltaTone === "down" && "text-destructive-ink",
            deltaTone === "flat" && "text-muted-foreground",
          )}
        >
          {delta}
        </p>
      ) : null}
      {hint ? (
        <p className="mt-2 text-xs text-muted-foreground">{hint}</p>
      ) : null}
      {pct !== null ? (
        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-brand transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}
