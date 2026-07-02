import { cn } from "@/lib/utils";

export type HealthLabel = "healthy" | "stale" | "at_risk" | "unused";

const HEALTH_CONFIG: Record<
  HealthLabel,
  { label: string; cls: string; dotCls: string }
> = {
  healthy: {
    label: "Healthy",
    cls: "bg-success-tint text-success-ink",
    dotCls: "bg-success",
  },
  stale: {
    label: "Stale",
    cls: "bg-amber-tint text-amber-ink",
    dotCls: "bg-amber",
  },
  at_risk: {
    label: "At risk",
    cls: "bg-destructive-soft text-destructive-ink",
    dotCls: "bg-destructive",
  },
  unused: {
    label: "Unused",
    cls: "bg-muted text-muted-foreground",
    dotCls: "bg-muted-foreground/60",
  },
};

interface HealthBadgeProps {
  label: HealthLabel | string | null | undefined;
  score?: number | null;
  className?: string;
}

export function HealthBadge({ label, score, className }: HealthBadgeProps) {
  if (!label) {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground",
          className,
        )}
        title="Health not yet computed"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
        Unscored
      </span>
    );
  }
  const cfg =
    HEALTH_CONFIG[label as HealthLabel] ?? HEALTH_CONFIG.unused;
  const pct = typeof score === "number" ? Math.round(score * 100) : null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
        cfg.cls,
        className,
      )}
      title={pct !== null ? `Health score: ${pct}/100` : undefined}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", cfg.dotCls)} />
      {cfg.label}
    </span>
  );
}
