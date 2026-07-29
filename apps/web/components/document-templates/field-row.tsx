"use client";

import { useState } from "react";
import { Check, ChevronDown, Loader2, Trash2, X } from "lucide-react";

import { StatusPill } from "@/components/actual/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  DocTemplateSlot,
  DocTemplateVariable,
  DocumentDataType,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const DATA_TYPES: { value: DocumentDataType; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "email", label: "Email" },
  { value: "phone", label: "Phone" },
  { value: "date", label: "Date" },
  { value: "currency", label: "Amount" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Yes / No" },
  { value: "address", label: "Address" },
  { value: "city", label: "City" },
  { value: "state", label: "State" },
  { value: "country", label: "Country" },
  { value: "designation", label: "Designation" },
  { value: "department", label: "Department" },
  { value: "manager", label: "Manager" },
  { value: "company", label: "Company" },
  { value: "signature_block", label: "Signature area" },
  { value: "custom", label: "Other" },
];

export function FieldRow({
  variable,
  slots,
  threshold,
  busy,
  onUpdate,
  onDelete,
}: {
  variable: DocTemplateVariable;
  slots: DocTemplateSlot[];
  threshold: number;
  busy: boolean;
  onUpdate: (patch: Record<string, unknown>) => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [name, setName] = useState(variable.display_name);

  const confident =
    variable.confidence !== null && variable.confidence >= threshold;
  const pending = variable.status === "proposed";

  return (
    <div
      className={cn(
        "rounded-xl border bg-card",
        pending && confident && "border-brand/40",
      )}
    >
      <div className="flex flex-wrap items-center gap-3 p-3">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
              expanded && "rotate-180",
            )}
          />
          <div className="min-w-0">
            <p className="truncate font-semibold">{variable.display_name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {slots.length} place{slots.length === 1 ? "" : "s"} in the document
              {variable.is_required ? " · required" : " · optional"}
            </p>
          </div>
        </button>

        {variable.confidence !== null ? (
          <StatusPill tone={confident ? "green" : "amber"}>
            {Math.round(variable.confidence * 100)}%
          </StatusPill>
        ) : (
          <StatusPill tone="blue">Added by you</StatusPill>
        )}

        <Select
          value={variable.data_type}
          onValueChange={(value) => onUpdate({ data_type: value })}
        >
          <SelectTrigger className="w-[150px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DATA_TYPES.map((type) => (
              <SelectItem key={type.value} value={type.value}>
                {type.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {pending ? (
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              onClick={() => onUpdate({ status: "confirmed" })}
              disabled={busy}
            >
              {busy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Check className="h-3 w-3" />
              )}
              <span className="ml-1.5">Confirm</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onUpdate({ status: "rejected" })}
              disabled={busy}
            >
              <X className="h-3 w-3" />
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onUpdate({ status: "proposed" })}
            disabled={busy}
          >
            Unconfirm
          </Button>
        )}
      </div>

      {expanded ? (
        <div className="space-y-4 border-t px-3 py-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`name-${variable.id}`}>Field name</Label>
              <Input
                id={`name-${variable.id}`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => {
                  if (name.trim() && name !== variable.display_name) {
                    onUpdate({ display_name: name.trim() });
                  }
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`default-${variable.id}`}>
                Default value (optional)
              </Label>
              <Input
                id={`default-${variable.id}`}
                defaultValue={variable.default_value ?? ""}
                placeholder="Used when nothing is supplied"
                onBlur={(e) => {
                  const value = e.target.value.trim();
                  if (value !== (variable.default_value ?? "")) {
                    onUpdate({ default_value: value });
                  }
                }}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={variable.is_required}
                onChange={(e) => onUpdate({ is_required: e.target.checked })}
                className="h-4 w-4 rounded border-input"
              />
              Required — block generation if this is missing
            </label>

            <Button
              size="sm"
              variant="ghost"
              onClick={onDelete}
              disabled={busy}
              className="ml-auto text-destructive hover:text-destructive"
            >
              <Trash2 className="mr-1.5 h-3 w-3" />
              Remove field
            </Button>
          </div>

          {slots.length > 0 ? (
            <div className="space-y-2">
              <p className="text-xs font-bold uppercase tracking-wide text-muted-foreground">
                Where this appears
              </p>
              <ul className="space-y-1.5">
                {slots.map((slot) => (
                  <li
                    key={slot.id}
                    className="rounded-lg bg-muted/50 px-3 py-2 text-xs"
                  >
                    <span className="text-muted-foreground">
                      {slot.context_before}
                    </span>
                    <mark className="rounded bg-brand-tint px-1 font-semibold text-brand">
                      {slot.original_text || "(blank)"}
                    </mark>
                    <span className="text-muted-foreground">
                      {slot.context_after}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-xs text-amber">
              This field isn&rsquo;t used anywhere in the document yet.
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
