import { getAction } from "@/lib/autoflow/catalog";
import { getTrigger } from "@/lib/autoflow/triggers";
import type { ActionStep, AutoflowDraft } from "@/lib/autoflow/types";

export interface ValidationResult {
  ok: boolean;
  errors: string[];
}

export function validateStep(step: ActionStep): ValidationResult {
  const entry = getAction(step.type);
  const errors: string[] = [];
  if (!entry.available) errors.push(`${entry.label} is not yet available.`);
  for (const f of entry.fields) {
    if (!f.required) continue;
    const v = step.config[f.key];
    if (v == null || (typeof v === "string" && v.trim() === "")) {
      errors.push(`${f.label} is required.`);
    }
  }
  return { ok: errors.length === 0, errors };
}

export function validateDraft(draft: AutoflowDraft): ValidationResult {
  const errors: string[] = [];
  if (!draft.name.trim()) errors.push("Name is required.");

  const t = getTrigger(draft.trigger_type);
  if (t.acceptsCron && !draft.trigger_config.cron) {
    errors.push("Scheduled trigger needs a cron expression.");
  }
  if (!t.acceptsCron && draft.trigger_config.cron) {
    errors.push(`Cron only applies to scheduled triggers (not ${t.label}).`);
  }

  if (draft.actions.length === 0) {
    errors.push("Add at least one action.");
  }

  for (const step of draft.actions) {
    const r = validateStep(step);
    if (!r.ok) errors.push(...r.errors.map((e) => `Step ${step.order + 1}: ${e}`));
  }

  if (draft.actions.length > 0) {
    const sorted = [...draft.actions].sort((a, b) => a.order - b.order);
    const last = sorted[sorted.length - 1];
    if (last.type === "hold_for_approval") {
      errors.push("hold_for_approval can't be the last step — put it before the action it gates.");
    }
  }

  if (
    draft.confidence_threshold != null &&
    (draft.confidence_threshold < 0 || draft.confidence_threshold > 1)
  ) {
    errors.push("Confidence threshold must be between 0 and 1.");
  }

  return { ok: errors.length === 0, errors };
}
