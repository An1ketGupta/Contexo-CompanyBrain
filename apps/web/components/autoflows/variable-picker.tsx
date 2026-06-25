"use client";

import { useMemo, useState } from "react";
import { Braces, Search } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getAction } from "@/lib/autoflow/catalog";
import { getTrigger } from "@/lib/autoflow/triggers";
import type { ActionStep, FieldSchema, TriggerType } from "@/lib/autoflow/types";

interface VariablePickerProps {
  triggerType: TriggerType;
  /** All steps in the flow; the picker shows outputs of steps with order < currentIndex. */
  steps: ActionStep[];
  currentIndex: number;
  onInsert: (token: string) => void;
  trigger?: React.ReactNode;
}

interface VariableGroup {
  groupId: string;
  label: string;
  hint: string;
  fields: Array<FieldSchema & { token: string }>;
}

export function VariablePicker({
  triggerType,
  steps,
  currentIndex,
  onInsert,
  trigger,
}: VariablePickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const groups = useMemo<VariableGroup[]>(() => {
    const result: VariableGroup[] = [];

    const tEntry = getTrigger(triggerType);
    result.push({
      groupId: "trigger",
      label: `Trigger · ${tEntry.label}`,
      hint: "Fields from the event that fired this flow.",
      fields: tEntry.payloadFields.map((f) => ({
        ...f,
        token: `{{trigger.${f.path}}}`,
      })),
    });

    const prior = steps
      .filter((s) => s.order < currentIndex)
      .sort((a, b) => a.order - b.order);
    for (const step of prior) {
      const entry = getAction(step.type);
      if (entry.outputFields.length === 0) continue;
      result.push({
        groupId: `step_${step.order}`,
        label: `Step ${step.order + 1} · ${entry.shortLabel} output`,
        hint: entry.label,
        fields: entry.outputFields.map((f) => ({
          ...f,
          token: `{{step_${step.order}.output.${f.path}}}`,
        })),
      });
    }
    return result;
  }, [triggerType, steps, currentIndex]);

  const filteredGroups = useMemo(() => {
    if (!query) return groups;
    const q = query.toLowerCase();
    return groups
      .map((g) => ({
        ...g,
        fields: g.fields.filter(
          (f) =>
            f.label.toLowerCase().includes(q) ||
            f.path.toLowerCase().includes(q) ||
            f.token.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.fields.length > 0);
  }, [groups, query]);

  const handleInsert = (token: string) => {
    onInsert(token);
    setOpen(false);
    setQuery("");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        {trigger ?? (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            className="h-7 gap-1 text-[11px] text-muted-foreground"
            title="Insert variable"
          >
            <Braces className="size-3" />
            Insert variable
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b p-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2 size-3.5 text-muted-foreground" />
            <Input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search variables…"
              className="h-8 pl-8 text-xs"
            />
          </div>
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {filteredGroups.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              No variables match.
            </p>
          ) : (
            filteredGroups.map((g) => (
              <div key={g.groupId} className="mb-2">
                <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {g.label}
                </div>
                <div className="space-y-0.5">
                  {g.fields.map((f) => (
                    <button
                      key={f.token}
                      type="button"
                      onClick={() => handleInsert(f.token)}
                      className={cn(
                        "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-xs hover:bg-accent",
                      )}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">{f.label}</div>
                        <div className="truncate font-mono text-[10px] text-muted-foreground">
                          {f.token}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
