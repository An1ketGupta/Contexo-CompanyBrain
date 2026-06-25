"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { CronBuilder } from "./cron-builder";
import { FilterBuilder } from "./filter-builder";
import { TRIGGER_CATALOG, TRIGGER_GROUPS, getTrigger } from "@/lib/autoflow/triggers";
import { getIcon } from "@/lib/autoflow/icons";
import type { AutoflowDraft, TriggerType } from "@/lib/autoflow/types";

interface TriggerFormProps {
  draft: AutoflowDraft;
  onUpdate: (next: AutoflowDraft) => void;
}

export function TriggerForm({ draft, onUpdate }: TriggerFormProps) {
  const entry = getTrigger(draft.trigger_type);
  const Icon = getIcon(entry.icon);

  const setTriggerType = (next: TriggerType) => {
    const t = getTrigger(next);
    onUpdate({
      ...draft,
      trigger_type: next,
      trigger_config: {
        ...(t.acceptsCron ? { cron: draft.trigger_config.cron ?? "0 9 * * *" } : {}),
        ...(t.acceptsFilters ? { filters: {} } : {}),
      },
    });
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
          <Icon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <Badge variant="outline" className="text-[10px] uppercase tracking-wide">
            Trigger
          </Badge>
          <p className="mt-1 text-sm font-medium">{entry.label}</p>
          <p className="text-xs text-muted-foreground">{entry.description}</p>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">Trigger type</Label>
        <Select value={draft.trigger_type} onValueChange={(v) => setTriggerType(v as TriggerType)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TRIGGER_GROUPS.map((g) => {
              const items = TRIGGER_CATALOG.filter((t) => t.group === g.id);
              if (!items.length) return null;
              return (
                <div key={g.id}>
                  <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {g.label}
                  </div>
                  {items.map((t) => (
                    <SelectItem key={t.type} value={t.type}>
                      {t.label}
                    </SelectItem>
                  ))}
                </div>
              );
            })}
          </SelectContent>
        </Select>
      </div>

      {entry.acceptsCron && (
        <CronBuilder
          value={draft.trigger_config.cron ?? ""}
          onChange={(cron) =>
            onUpdate({
              ...draft,
              trigger_config: { ...draft.trigger_config, cron },
            })
          }
        />
      )}

      {entry.acceptsFilters && (
        <FilterBuilder
          value={draft.trigger_config.filters ?? {}}
          onChange={(filters) =>
            onUpdate({
              ...draft,
              trigger_config: { ...draft.trigger_config, filters },
            })
          }
          fields={entry.payloadFields}
        />
      )}

      <div className="space-y-1.5">
        <Label className="text-xs font-medium">Confidence gate (0–1, optional)</Label>
        <Input
          type="number"
          step="0.05"
          min={0}
          max={1}
          value={draft.confidence_threshold ?? ""}
          onChange={(e) =>
            onUpdate({
              ...draft,
              confidence_threshold: e.target.value === "" ? null : Number(e.target.value),
            })
          }
          placeholder="e.g. 0.6"
          className="w-32"
        />
        <p className="text-[11px] text-muted-foreground">
          When set, AI outputs scoring below this confidence pause for admin approval.
        </p>
      </div>
    </div>
  );
}
