"use client";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FieldWithVariables } from "../field-with-variables";
import { SlackChannelPicker } from "./slack-channel-picker";
import { TagsPicker } from "./tags-picker";
import { validateStep } from "../validation";
import { getAction, WEBHOOK_EVENT_OPTIONS, type ActionField } from "@/lib/autoflow/catalog";
import { getIcon } from "@/lib/autoflow/icons";
import type { ActionStep, AutoflowDraft } from "@/lib/autoflow/types";

interface ActionFormProps {
  step: ActionStep;
  draft: AutoflowDraft;
  onUpdate: (next: ActionStep) => void;
}

const CATEGORY_STYLES = {
  ai: "bg-violet-100 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300",
  notify: "bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
  integrations: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
  control: "bg-orange-100 text-orange-700 dark:bg-orange-950/50 dark:text-orange-300",
};

export function ActionForm({ step, draft, onUpdate }: ActionFormProps) {
  const entry = getAction(step.type);
  const Icon = getIcon(entry.icon);

  const setField = (key: string, value: unknown) => {
    onUpdate({ ...step, config: { ...step.config, [key]: value } });
  };

  const validation = validateStep(step);

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${CATEGORY_STYLES[entry.category]}`}>
          <Icon className="size-5" />
        </div>
        <div className="min-w-0 flex-1">
          <Badge variant="outline" className="text-[10px] uppercase tracking-wide">
            Step {step.order + 1}
          </Badge>
          <p className="mt-1 text-sm font-medium">{entry.label}</p>
          <p className="text-xs text-muted-foreground">{entry.description}</p>
        </div>
      </div>

      {!entry.available && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
          {entry.label} is reserved for a future sprint. Saving a flow that uses it will fail at runtime.
        </div>
      )}

      <div className="space-y-4">
        {entry.fields.map((f) => (
          <FieldRenderer
            key={f.key}
            field={f}
            value={step.config[f.key]}
            onChange={(v) => setField(f.key, v)}
            step={step}
            draft={draft}
          />
        ))}
      </div>

      {!validation.ok && (
        <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
          <p className="font-medium">Needs setup</p>
          <ul className="mt-1 space-y-0.5">
            {validation.errors.map((e, i) => (
              <li key={i}>• {e}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

interface FieldRendererProps {
  field: ActionField;
  value: unknown;
  onChange: (v: unknown) => void;
  step: ActionStep;
  draft: AutoflowDraft;
}

function FieldRenderer({ field, value, onChange, step, draft }: FieldRendererProps) {
  const text = value == null ? "" : String(value);

  switch (field.type) {
    case "text":
      return field.supportsVariables ? (
        <FieldWithVariables
          label={field.label}
          value={text}
          onChange={onChange as (v: string) => void}
          description={field.description}
          placeholder={field.placeholder}
          required={field.required}
          triggerType={draft.trigger_type}
          steps={draft.actions}
          currentIndex={step.order}
        />
      ) : (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">
            {field.label} {field.required && <span className="text-destructive">*</span>}
          </Label>
          <Input
            value={text}
            onChange={(e) => onChange(e.target.value)}
            placeholder={field.placeholder}
          />
          {field.description && (
            <p className="text-[11px] text-muted-foreground">{field.description}</p>
          )}
        </div>
      );

    case "textarea":
      return field.supportsVariables ? (
        <FieldWithVariables
          label={field.label}
          value={text}
          onChange={onChange as (v: string) => void}
          description={field.description}
          placeholder={field.placeholder}
          multiline
          rows={field.rows}
          required={field.required}
          triggerType={draft.trigger_type}
          steps={draft.actions}
          currentIndex={step.order}
        />
      ) : (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">
            {field.label} {field.required && <span className="text-destructive">*</span>}
          </Label>
          <Textarea
            rows={field.rows ?? 4}
            value={text}
            onChange={(e) => onChange(e.target.value)}
            placeholder={field.placeholder}
          />
          {field.description && (
            <p className="text-[11px] text-muted-foreground">{field.description}</p>
          )}
        </div>
      );

    case "select": {
      const EMPTY = "__none__";
      const display = text === "" ? EMPTY : text;
      return (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">
            {field.label} {field.required && <span className="text-destructive">*</span>}
          </Label>
          <Select value={display} onValueChange={(v) => onChange(v === EMPTY ? "" : v)}>
            <SelectTrigger>
              <SelectValue placeholder="Choose…" />
            </SelectTrigger>
            <SelectContent>
              {(field.options ?? []).map((opt) => (
                <SelectItem key={opt.value || EMPTY} value={opt.value || EMPTY}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {field.description && (
            <p className="text-[11px] text-muted-foreground">{field.description}</p>
          )}
        </div>
      );
    }

    case "slack-channel":
      return (
        <SlackChannelPicker
          value={text}
          onChange={onChange as (v: string) => void}
          description={field.description}
          required={field.required}
        />
      );

    case "tags":
      return (
        <TagsPicker
          value={Array.isArray(value) ? (value as string[]) : []}
          onChange={onChange as (v: string[]) => void}
          description={field.description}
          label={field.label}
        />
      );

    case "users":
      return (
        <FieldWithVariables
          label={field.label}
          value={text}
          onChange={onChange as (v: string) => void}
          description={field.description ?? "Optional — leave empty to fan out to all admins."}
          placeholder="user uuid"
          required={field.required}
          triggerType={draft.trigger_type}
          steps={draft.actions}
          currentIndex={step.order}
          monospace
        />
      );

    case "notion-page":
      return (
        <FieldWithVariables
          label={field.label}
          value={text}
          onChange={onChange as (v: string) => void}
          description={field.description ?? "Paste the parent page ID from Notion (Share → Copy link → the trailing ID)."}
          placeholder="32-char Notion page id"
          required={field.required}
          triggerType={draft.trigger_type}
          steps={draft.actions}
          currentIndex={step.order}
          monospace
        />
      );

    case "webhook-event":
      return (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">
            {field.label} {field.required && <span className="text-destructive">*</span>}
          </Label>
          <Input
            value={text}
            onChange={(e) => onChange(e.target.value)}
            placeholder={field.placeholder}
            list="webhook-events"
            className="font-mono text-xs"
          />
          <datalist id="webhook-events">
            {WEBHOOK_EVENT_OPTIONS.map((e) => (
              <option key={e} value={e} />
            ))}
          </datalist>
          {field.description && (
            <p className="text-[11px] text-muted-foreground">{field.description}</p>
          )}
        </div>
      );

    case "json": {
      const display =
        typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2);
      return (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">{field.label}</Label>
          <Textarea
            rows={4}
            value={display}
            onChange={(e) => {
              try {
                onChange(JSON.parse(e.target.value || "{}"));
              } catch {
                onChange(e.target.value);
              }
            }}
            placeholder='{"key": "value"}'
            className="font-mono text-xs"
          />
          {field.description && (
            <p className="text-[11px] text-muted-foreground">{field.description}</p>
          )}
        </div>
      );
    }
  }
}
