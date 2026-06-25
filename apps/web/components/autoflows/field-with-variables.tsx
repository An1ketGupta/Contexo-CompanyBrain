"use client";

import { useRef } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { VariablePicker } from "./variable-picker";
import type { ActionStep, TriggerType } from "@/lib/autoflow/types";
import { cn } from "@/lib/utils";

interface FieldWithVariablesProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  description?: string;
  placeholder?: string;
  multiline?: boolean;
  rows?: number;
  required?: boolean;
  triggerType: TriggerType;
  steps: ActionStep[];
  currentIndex: number;
  monospace?: boolean;
}

export function FieldWithVariables({
  label,
  value,
  onChange,
  description,
  placeholder,
  multiline,
  rows = 4,
  required,
  triggerType,
  steps,
  currentIndex,
  monospace,
}: FieldWithVariablesProps) {
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  const insertAtCursor = (token: string) => {
    const el = inputRef.current;
    if (!el) {
      onChange(value + token);
      return;
    }
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    const next = value.slice(0, start) + token + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      el.focus();
      const cursor = start + token.length;
      try {
        el.setSelectionRange(cursor, cursor);
      } catch {
        // some browsers throw on Textarea before mount; ignore
      }
    });
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-medium">
          {label} {required && <span className="text-destructive">*</span>}
        </Label>
        <VariablePicker
          triggerType={triggerType}
          steps={steps}
          currentIndex={currentIndex}
          onInsert={insertAtCursor}
        />
      </div>
      {multiline ? (
        <Textarea
          ref={inputRef as React.RefObject<HTMLTextAreaElement>}
          rows={rows}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cn(monospace && "font-mono text-xs")}
        />
      ) : (
        <Input
          ref={inputRef as React.RefObject<HTMLInputElement>}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cn(monospace && "font-mono text-xs")}
        />
      )}
      {description && (
        <p className="text-[11px] text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
