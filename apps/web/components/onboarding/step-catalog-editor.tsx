"use client";

import { useState } from "react";
import { GripVertical, Loader2, Lock, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  type CatalogStep,
  type CollectItem,
  type NewCollectStep,
  useOnboardingCatalog,
} from "@/hooks/use-onboarding-catalog";

/** Formats the backend will accept, mirrored for the picker. */
const FORMATS = ["pdf", "jpg", "jpeg", "png", "doc", "docx"] as const;

const DEFAULT_FORMATS = ["pdf", "jpg", "jpeg", "png"];

/**
 * Documents companies ask for most often. Offered as one-tap suggestions so
 * the common case is a few clicks rather than typing the same six labels every
 * time — the list is a starting point, not a constraint.
 */
const COMMON_DOCUMENTS = [
  "10th marksheet",
  "12th marksheet",
  "Graduation certificate",
  "PAN card",
  "Aadhaar card",
  "Relieving letter",
  "Last 3 payslips",
  "Cancelled cheque",
  "Passport-size photo",
];

type DraftItem = Omit<CollectItem, "item_key"> & { item_key?: string };

function newItem(label = ""): DraftItem {
  return {
    label,
    help_text: null,
    required: true,
    accepted_formats: [...DEFAULT_FORMATS],
  };
}

/**
 * The org's onboarding pipeline, as a list it can edit.
 *
 * Replaces the four on/off switches this started as. What an org can change
 * here is deliberately narrower than what the data model supports: steps can
 * be enabled, disabled, and — for document collection — added, edited and
 * placed. Reordering the built-in document steps and choosing who signs them
 * are not offered yet, because the agent still runs those in a fixed order
 * with fixed signers; a control that silently did nothing would be worse than
 * its absence.
 */
export function StepCatalogEditor({
  canEdit,
  onSaved,
}: {
  canEdit: boolean;
  onSaved?: () => void;
}) {
  const {
    steps,
    bundles,
    isLoading,
    error,
    setStepEnabled,
    addCollectStep,
    replaceItems,
    removeStep,
  } = useOnboardingCatalog();

  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  async function run(key: string, action: () => Promise<unknown>, ok: string) {
    setBusy(key);
    try {
      await action();
      toast.success(ok);
      onSaved?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) {
    return <div className="h-40 animate-pulse rounded-xl bg-muted" />;
  }
  if (error) {
    return (
      <p className="text-sm text-destructive-ink">
        Couldn&apos;t load your onboarding flow. Try again later.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {bundles.map((group) => {
        const lead = group[0];
        const key = lead.bundle_key ?? lead.step_key;
        const label = lead.bundle_label ?? lead.label;
        const isCollect = lead.kind === "collect";
        const pending = busy === key;

        return (
          <div
            key={key}
            className={cn(
              "rounded-xl border p-4 transition-colors",
              lead.enabled
                ? "border-border bg-card"
                : "border-dashed border-border bg-muted/30",
            )}
          >
            <div className="flex items-start gap-3">
              <span aria-hidden className="mt-0.5 text-muted-foreground">
                {lead.locked ? (
                  <Lock className="h-4 w-4" />
                ) : (
                  <GripVertical className="h-4 w-4 opacity-40" />
                )}
              </span>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold text-foreground">{label}</p>
                  {isCollect ? (
                    <span className="rounded-full bg-brand-tint px-2 py-0.5 text-[11px] font-medium text-brand">
                      Documents from candidate
                    </span>
                  ) : null}
                  {group.length > 1 ? (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                      Sent together
                    </span>
                  ) : null}
                </div>

                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  {lead.locked
                    ? "Always runs first — every later step builds on what it produces."
                    : isCollect
                      ? `${lead.items.length} document${lead.items.length === 1 ? "" : "s"}: ${lead.items
                          .map((i) => i.label)
                          .join(", ")}`
                      : (lead.description ?? "")}
                </p>

                {isCollect && editing === lead.step_key ? (
                  <ChecklistEditor
                    initial={lead.items}
                    saving={pending}
                    onCancel={() => setEditing(null)}
                    onSave={async (items) => {
                      await run(
                        key,
                        () => replaceItems(lead.step_key, items),
                        "Document list updated.",
                      );
                      setEditing(null);
                    }}
                  />
                ) : null}

                {isCollect && editing !== lead.step_key && canEdit ? (
                  <div className="mt-2 flex gap-3">
                    <button
                      type="button"
                      onClick={() => setEditing(lead.step_key)}
                      className="text-xs font-medium text-brand hover:underline"
                    >
                      Edit documents
                    </button>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() =>
                        run(
                          key,
                          () => removeStep(lead.step_key),
                          "Step removed.",
                        )
                      }
                      className="text-xs font-medium text-destructive-ink hover:underline disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </div>
                ) : null}
              </div>

              {lead.locked ? (
                <span className="text-xs text-muted-foreground">Always on</span>
              ) : (
                <button
                  type="button"
                  role="switch"
                  aria-checked={lead.enabled}
                  aria-label={label}
                  disabled={!canEdit || pending}
                  onClick={() =>
                    run(
                      key,
                      () => setStepEnabled(lead.step_key, !lead.enabled),
                      lead.enabled ? "Step turned off." : "Step turned on.",
                    )
                  }
                  className={cn(
                    "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border-2 border-transparent transition-colors",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    lead.enabled ? "bg-brand" : "bg-muted",
                  )}
                >
                  <span
                    aria-hidden
                    className={cn(
                      "inline-block h-5 w-5 transform rounded-full bg-background shadow transition-transform",
                      lead.enabled ? "translate-x-5" : "translate-x-0",
                    )}
                  />
                </button>
              )}
            </div>
          </div>
        );
      })}

      {canEdit ? (
        adding ? (
          <AddCollectStep
            steps={steps}
            onCancel={() => setAdding(false)}
            onSave={async (step) => {
              await run("new", () => addCollectStep(step), "Step added.");
              setAdding(false);
            }}
            saving={busy === "new"}
          />
        ) : (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border py-3 text-sm font-medium text-muted-foreground transition-colors hover:border-brand hover:text-brand"
          >
            <Plus className="h-4 w-4" />
            Ask the candidate for documents
          </button>
        )
      ) : null}
    </div>
  );
}

/** Compose a new document-collection step and choose where it runs. */
function AddCollectStep({
  steps,
  onSave,
  onCancel,
  saving,
}: {
  steps: CatalogStep[];
  onSave: (step: NewCollectStep) => Promise<void>;
  onCancel: () => void;
  saving: boolean;
}) {
  const [label, setLabel] = useState("Joining documents");
  const [after, setAfter] = useState<string>("loi");
  const [items, setItems] = useState<DraftItem[]>([newItem("10th marksheet")]);

  const valid = label.trim() && items.some((i) => i.label.trim());

  return (
    <div className="rounded-xl border border-brand bg-brand-tint/20 p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground">
          Ask the candidate for documents
        </p>
        <button type="button" onClick={onCancel} aria-label="Cancel">
          <X className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs font-medium text-foreground">Step name</span>
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Joining documents"
            className="mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-foreground">Ask after</span>
          <select
            value={after}
            onChange={(e) => setAfter(e.target.value)}
            className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
          >
            {steps
              .filter((s) => s.kind !== "collect")
              .map((s) => (
                <option key={s.step_key} value={s.step_key}>
                  {s.label}
                </option>
              ))}
          </select>
        </label>
      </div>

      <ChecklistFields items={items} onChange={setItems} />

      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button
          disabled={!valid || saving}
          onClick={() =>
            onSave({
              label: label.trim(),
              after_step_key: after,
              items: items.filter((i) => i.label.trim()),
            })
          }
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Add step
        </Button>
      </div>
    </div>
  );
}

/** Edit an existing step's checklist in place. */
function ChecklistEditor({
  initial,
  onSave,
  onCancel,
  saving,
}: {
  initial: CollectItem[];
  onSave: (items: DraftItem[]) => Promise<void>;
  onCancel: () => void;
  saving: boolean;
}) {
  const [items, setItems] = useState<DraftItem[]>(
    initial.length ? initial.map((i) => ({ ...i })) : [newItem()],
  );

  return (
    <div className="mt-3 rounded-lg border border-border bg-background p-3">
      <ChecklistFields items={items} onChange={setItems} />
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button
          size="sm"
          disabled={saving || !items.some((i) => i.label.trim())}
          onClick={() => onSave(items.filter((i) => i.label.trim()))}
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Save
        </Button>
      </div>
    </div>
  );
}

/** The checklist rows themselves — shared by the add and edit paths. */
function ChecklistFields({
  items,
  onChange,
}: {
  items: DraftItem[];
  onChange: (items: DraftItem[]) => void;
}) {
  const used = new Set(items.map((i) => i.label.trim().toLowerCase()));
  const suggestions = COMMON_DOCUMENTS.filter(
    (d) => !used.has(d.toLowerCase()),
  ).slice(0, 6);

  const patch = (index: number, next: Partial<DraftItem>) =>
    onChange(items.map((it, i) => (i === index ? { ...it, ...next } : it)));

  return (
    <div className="mt-3 space-y-2">
      {items.map((item, index) => (
        <div
          key={index}
          className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-2"
        >
          <Input
            value={item.label}
            onChange={(e) => patch(index, { label: e.target.value })}
            placeholder="Document name"
            className="h-8 min-w-40 flex-1"
          />

          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={item.required}
              onChange={(e) => patch(index, { required: e.target.checked })}
              className="h-3.5 w-3.5 accent-[var(--brand)]"
            />
            Required
          </label>

          <select
            value={item.accepted_formats.join(",")}
            onChange={(e) =>
              patch(index, { accepted_formats: e.target.value.split(",") })
            }
            className="h-8 rounded-md border border-border bg-background px-2 text-xs"
            aria-label={`Accepted file types for ${item.label || "document"}`}
          >
            <option value={DEFAULT_FORMATS.join(",")}>PDF or image</option>
            <option value="pdf">PDF only</option>
            <option value="jpg,jpeg,png">Image only</option>
            <option value={FORMATS.join(",")}>Any document</option>
          </select>

          {items.length > 1 ? (
            <button
              type="button"
              onClick={() => onChange(items.filter((_, i) => i !== index))}
              aria-label={`Remove ${item.label || "document"}`}
            >
              <Trash2 className="h-4 w-4 text-muted-foreground hover:text-destructive-ink" />
            </button>
          ) : null}
        </div>
      ))}

      <button
        type="button"
        onClick={() => onChange([...items, newItem()])}
        className="flex items-center gap-1.5 text-xs font-medium text-brand hover:underline"
      >
        <Plus className="h-3.5 w-3.5" />
        Add another document
      </button>

      {suggestions.length ? (
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <span className="text-[11px] text-muted-foreground">Common:</span>
          {suggestions.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => {
                const blank = items.findIndex((i) => !i.label.trim());
                if (blank >= 0) patch(blank, { label: name });
                else onChange([...items, newItem(name)]);
              }}
              className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-brand hover:text-brand"
            >
              + {name}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
