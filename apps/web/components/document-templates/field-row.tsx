"use client";

import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, Trash2, X } from "lucide-react";

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

const SNIPPET_CONTEXT_CHARS = 90;

/** Trim from the left, cutting at a word boundary so we never open mid-word. */
function truncateStart(text: string, maxChars: number) {
  const trimmed = text.trimStart();
  if (trimmed.length <= maxChars) return trimmed;
  const tail = trimmed.slice(trimmed.length - maxChars);
  const space = tail.indexOf(" ");
  return `…${space === -1 ? tail : tail.slice(space + 1)}`;
}

/** Trim from the right, cutting at a word boundary. */
function truncateEnd(text: string, maxChars: number) {
  const trimmed = text.trimEnd();
  if (trimmed.length <= maxChars) return trimmed;
  const head = trimmed.slice(0, maxChars);
  const space = head.lastIndexOf(" ");
  return `${space === -1 ? head : head.slice(0, space)}…`;
}

const PARAGRAPH_KIND_LABELS: Record<DocTemplateSlot["paragraph_kind"], string> = {
  body: "",
  table: "in a table",
  header: "in the header",
  footer: "in the footer",
};

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
  const [slotIndex, setSlotIndex] = useState(0);

  const confident =
    variable.confidence !== null && variable.confidence >= threshold;
  const pending = variable.status === "proposed";
  const previewSlot = slots[slotIndex % Math.max(slots.length, 1)];

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

      {!expanded && previewSlot ? (
        <div className="flex items-start gap-3 border-t px-3 py-2.5">
          <p className="min-w-0 flex-1 border-l-2 border-border pl-2.5 text-xs leading-relaxed text-muted-foreground">
            {truncateStart(previewSlot.context_before, SNIPPET_CONTEXT_CHARS)}
            <span className="mx-1 rounded border border-brand/30 bg-brand-tint px-1.5 py-0.5 font-mono text-[11px] font-medium text-brand">
              {previewSlot.original_text.trim() || "(blank)"}
            </span>
            {truncateEnd(previewSlot.context_after, SNIPPET_CONTEXT_CHARS)}
            {PARAGRAPH_KIND_LABELS[previewSlot.paragraph_kind] ? (
              <span className="ml-1.5 italic opacity-70">
                {PARAGRAPH_KIND_LABELS[previewSlot.paragraph_kind]}
              </span>
            ) : null}
          </p>

          {slots.length > 1 ? (
            <button
              type="button"
              onClick={() => setSlotIndex((i) => (i + 1) % slots.length)}
              className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {slotIndex + 1} of {slots.length}
              <ChevronRight className="h-3 w-3" />
            </button>
          ) : null}
        </div>
      ) : null}

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
