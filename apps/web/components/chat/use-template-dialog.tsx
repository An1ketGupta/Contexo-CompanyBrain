"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { PromptTemplate } from "@/lib/types";

interface UseTemplateDialogProps {
  template: PromptTemplate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the resolved prompt text once the user clicks Insert. */
  onResolved: (text: string) => void;
}

/**
 * V4 #70 — collect values for the template's `{{variables}}` and ask the
 * backend to resolve. Returns the resolved prompt to the caller, which then
 * decides what to do with it (inject into the chat composer, navigate to a
 * new conversation, etc.).
 *
 * For templates with zero variables we skip the dialog round-trip and
 * directly invoke `onResolved` with the raw template_text — that path is
 * handled by the caller (TemplatePopover), so this component assumes at
 * least one variable.
 */
export function UseTemplateDialog({
  template,
  open,
  onOpenChange,
  onResolved,
}: UseTemplateDialogProps) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Reset whenever the dialog opens for a fresh template.
    if (open && template) {
      const initial: Record<string, string> = {};
      for (const v of template.variables ?? []) initial[v.name] = "";
      setValues(initial);
    }
  }, [open, template]);

  const submit = async () => {
    if (!template) return;
    setSubmitting(true);
    try {
      const res = await fetch(`/api/templates/${template.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variable_values: values }),
      });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (!res.ok) {
        throw new Error(
          (body.detail as string) || `Failed to resolve (${res.status})`,
        );
      }
      const resolved = body.resolved_prompt as string;
      onResolved(resolved);
      onOpenChange(false);
    } catch (err) {
      toast.error((err as Error).message || "Couldn't fill the template.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!template) return null;

  const variables = template.variables ?? [];
  const allRequiredFilled = variables
    .filter((v) => v.required)
    .every((v) => (values[v.name] || "").trim().length > 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{template.title}</DialogTitle>
          {template.description ? (
            <DialogDescription>{template.description}</DialogDescription>
          ) : null}
        </DialogHeader>
        <div className="space-y-4">
          {variables.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This template has no variables.
            </p>
          ) : (
            variables.map((v) => (
              <div key={v.name} className="space-y-1.5">
                <Label htmlFor={`tplvar-${v.name}`}>
                  {v.label}
                  {v.required ? (
                    <span className="ml-0.5 text-destructive">*</span>
                  ) : null}
                </Label>
                {looksLong(v) ? (
                  <Textarea
                    id={`tplvar-${v.name}`}
                    value={values[v.name] || ""}
                    onChange={(e) =>
                      setValues((prev) => ({ ...prev, [v.name]: e.target.value }))
                    }
                    placeholder={v.placeholder || ""}
                    rows={3}
                    disabled={submitting}
                  />
                ) : (
                  <Input
                    id={`tplvar-${v.name}`}
                    value={values[v.name] || ""}
                    onChange={(e) =>
                      setValues((prev) => ({ ...prev, [v.name]: e.target.value }))
                    }
                    placeholder={v.placeholder || ""}
                    disabled={submitting}
                  />
                )}
              </div>
            ))
          )}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || !allRequiredFilled}>
            {submitting ? (
              <>
                <Loader2 className="animate-spin" /> Resolving…
              </>
            ) : (
              "Use template"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Heuristic — long labels or "topics"/"context" hints get a textarea. */
function looksLong(v: {
  label?: string;
  placeholder?: string;
  name: string;
}): boolean {
  const text = `${v.label ?? ""} ${v.placeholder ?? ""} ${v.name}`.toLowerCase();
  return /(topic|context|notes|description|details|message|body)/.test(text);
}
