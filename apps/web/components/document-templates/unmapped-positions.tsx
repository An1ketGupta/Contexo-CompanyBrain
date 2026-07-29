"use client";

import { Loader2, X } from "lucide-react";

import { StatusPill } from "@/components/actual/kit";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { DocTemplateSlot, DocTemplateVariable } from "@/lib/types";

/**
 * Positions we found but couldn't name.
 *
 * These matter more than they look: a fill-point nobody assigns is a blank in
 * a signed contract. Detection deliberately over-flags, so this list is the
 * place HR either gives a spot a meaning or explicitly says it isn't one.
 */
export function UnmappedPositions({
  slots,
  variables,
  busy,
  onAssign,
  onReject,
}: {
  slots: DocTemplateSlot[];
  variables: DocTemplateVariable[];
  busy: string | null;
  onAssign: (slotId: string, variableId: string) => void;
  onReject: (slotId: string) => void;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-bold uppercase tracking-wide text-muted-foreground">
          Unassigned positions
        </h2>
        <StatusPill tone="amber">{slots.length}</StatusPill>
      </div>
      <p className="text-sm text-muted-foreground">
        We found these spots but couldn&rsquo;t work out what belongs in them.
        Pick a field, or dismiss the ones that aren&rsquo;t really fill-in spots.
      </p>

      <div className="space-y-2">
        {slots.map((slot) => (
          <div
            key={slot.id}
            className="flex flex-wrap items-center gap-3 rounded-xl border bg-card p-3"
          >
            <p className="min-w-0 flex-1 text-xs">
              <span className="text-muted-foreground">
                {slot.context_before}
              </span>
              <mark className="rounded bg-amber-tint px-1 font-semibold text-amber">
                {slot.original_text || "(blank)"}
              </mark>
              <span className="text-muted-foreground">{slot.context_after}</span>
            </p>

            <Select
              onValueChange={(variableId) => onAssign(slot.id, variableId)}
              disabled={busy === slot.id || variables.length === 0}
            >
              <SelectTrigger className="w-[200px]">
                <SelectValue
                  placeholder={
                    variables.length === 0
                      ? "Confirm a field first"
                      : "Assign a field"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {variables.map((variable) => (
                  <SelectItem key={variable.id} value={variable.id}>
                    {variable.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button
              size="sm"
              variant="ghost"
              onClick={() => onReject(slot.id)}
              disabled={busy === slot.id}
            >
              {busy === slot.id ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <X className="h-3 w-3" />
              )}
              <span className="ml-1.5">Not a field</span>
            </Button>
          </div>
        ))}
      </div>
    </section>
  );
}
