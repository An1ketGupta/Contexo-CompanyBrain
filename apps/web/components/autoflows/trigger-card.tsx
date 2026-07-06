"use client";

import { Zap } from "lucide-react";
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
        "group relative w-full rounded-2xl border border-border bg-card p-5 text-left shadow-sm transition-all hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected && "border-brand ring-2 ring-brand/25",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-amber-tint text-amber-ink">
          <Icon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-amber-ink">
              <Zap className="size-3" />
              When
            </span>
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">Trigger</span>
          </div>
          <p className="mt-2 text-sm font-semibold">{summary.headline}</p>
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
