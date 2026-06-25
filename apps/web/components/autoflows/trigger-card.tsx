"use client";

import { Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { getTrigger } from "@/lib/autoflow/triggers";
import { getIcon } from "@/lib/autoflow/icons";
import type { TriggerConfig, TriggerType } from "@/lib/autoflow/types";

interface TriggerCardProps {
  triggerType: TriggerType;
  triggerConfig: TriggerConfig;
  selected: boolean;
  onClick: () => void;
}

export function TriggerCard({ triggerType, triggerConfig, selected, onClick }: TriggerCardProps) {
  const entry = getTrigger(triggerType);
  const Icon = getIcon(entry.icon);
  const summary = buildTriggerSummary(entry.label, triggerConfig);

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative w-full rounded-xl border bg-card p-4 text-left shadow-sm transition-all hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected && "border-primary ring-2 ring-primary/30",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
          <Icon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="gap-1 text-[10px] font-semibold uppercase tracking-wide">
              <Zap className="size-3" />
              When
            </Badge>
            <span className="text-xs text-muted-foreground">Trigger</span>
          </div>
          <p className="mt-2 text-sm font-medium">{summary.headline}</p>
          {summary.detail && (
            <p className="mt-1 text-xs text-muted-foreground">{summary.detail}</p>
          )}
        </div>
      </div>
    </button>
  );
}

function buildTriggerSummary(label: string, cfg: TriggerConfig) {
  const headline = label;
  const bits: string[] = [];
  if (cfg.cron) bits.push(`cron: ${cfg.cron}`);
  if (cfg.filters && Object.keys(cfg.filters).length > 0) {
    const filterStr = Object.entries(cfg.filters)
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
      .join(" · ");
    bits.push(`filters: ${filterStr}`);
  }
  return { headline, detail: bits.join(" · ") || null };
}
