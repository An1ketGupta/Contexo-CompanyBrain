"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  FileSignature,
  Loader2,
  Lock,
  Plus,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  type CatalogStep,
  type CollectItem,
  type DocumentType,
  type DraftItem,
  type NewStep,
  type SignerRole,
  type StepKind,
  type SystemAction,
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

const SIGNER_LABELS: Record<SignerRole, string> = {
  hr: "HR",
  candidate: "Candidate",
};

/** What you can add to the pipeline, in the order the chooser offers it. */
const STEP_TYPES: Array<{
  id: string;
  kind: StepKind;
  system_action?: SystemAction;
  title: string;
  blurb: string;
  Icon: typeof Upload;
  defaultLabel: string;
}> = [
  {
    id: "collect",
    kind: "collect",
    title: "Ask for documents",
    blurb:
      "The candidate uploads a checklist you define — marksheets, PAN, relieving letter.",
    Icon: Upload,
    defaultLabel: "Joining documents",
  },
  {
    id: "generate",
    kind: "generate",
    title: "Send official documents",
    blurb:
      "Render your templates — offer letter, appointment letter, NDA — and choose who signs.",
    Icon: FileSignature,
    defaultLabel: "Offer paperwork",
  },
  {
    id: "bgv",
    kind: "system",
    system_action: "bgv",
    title: "Run a background check",
    blurb:
      "Ask the candidate for referees, then email each one a verification form.",
    Icon: ShieldCheck,
    defaultLabel: "Background verification",
  },
  {
    id: "policies",
    kind: "system",
    system_action: "policies",
    title: "Get policy sign-off",
    blurb:
      "Assign the policies that need acknowledging and wait for the candidate.",
    Icon: ShieldCheck,
    defaultLabel: "Policy acknowledgement",
  },
];

function newItem(label = ""): DraftItem {
  return {
    label,
    help_text: null,
    required: true,
    accepted_formats: [...DEFAULT_FORMATS],
  };
}

/**
 * The org's onboarding pipeline, as a list it composes.
 *
 * Every step is addable, movable and removable — including the letter of
 * intent, which stopped being special once run creation took over the
 * candidate's account and the background-check step took over the references
 * token. What the list says is what the agent runs.
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
    documentTypes,
    isLoading,
    error,
    setStepEnabled,
    setSigners,
    moveStep,
    addStep,
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
      {bundles.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          Your onboarding runs nothing yet. Add a first step below.
        </p>
      ) : null}

      {bundles.map((group, index) => {
        const lead = group[0];
        const key = lead.bundle_key ?? lead.step_key;
        const label = lead.bundle_label ?? lead.label;
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
              {lead.locked ? (
                <span
                  aria-hidden
                  className="mt-0.5 text-muted-foreground"
                  title="This step always runs first"
                >
                  <Lock className="h-4 w-4" />
                </span>
              ) : (
                <Reorder
                  disabled={!canEdit || pending}
                  isFirst={index === 0}
                  isLast={index === bundles.length - 1}
                  onMove={(dir) =>
                    run(key, () => moveStep(lead.step_key, dir), "Step moved.")
                  }
                />
              )}

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold text-foreground">{label}</p>
                  <StepBadge step={lead} members={group.length} />
                </div>

                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  {describe(lead, group)}
                </p>

                {lead.kind === "generate" ? (
                  <SignerPicker
                    roles={lead.signer_roles as SignerRole[]}
                    disabled={!canEdit || pending}
                    onChange={(roles) =>
                      run(
                        key,
                        () => setSigners(lead.step_key, roles),
                        roles.length
                          ? `Signed by ${roles.map((r) => SIGNER_LABELS[r]).join(", then ")}.`
                          : "Sent without signatures.",
                      )
                    }
                  />
                ) : null}

                {lead.kind === "collect" && editing === lead.step_key ? (
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

                {canEdit && editing !== lead.step_key ? (
                  <div className="mt-2 flex gap-3">
                    {lead.kind === "collect" ? (
                      <button
                        type="button"
                        onClick={() => setEditing(lead.step_key)}
                        className="text-xs font-medium text-brand hover:underline"
                      >
                        Edit documents
                      </button>
                    ) : null}
                    {lead.locked ? null : (
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() =>
                          run(key, () => removeStep(lead.step_key), "Step removed.")
                        }
                        className="text-xs font-medium text-destructive-ink hover:underline disabled:opacity-50"
                      >
                        Remove
                      </button>
                    )}
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
          <AddStep
            steps={steps}
            documentTypes={documentTypes}
            saving={busy === "new"}
            onCancel={() => setAdding(false)}
            onSave={async (step) => {
              await run("new", () => addStep(step), "Step added.");
              setAdding(false);
            }}
          />
        ) : (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border py-3 text-sm font-medium text-muted-foreground transition-colors hover:border-brand hover:text-brand"
          >
            <Plus className="h-4 w-4" />
            {bundles.length === 0 ? "Add the first step" : "Add a step"}
          </button>
        )
      ) : null}
    </div>
  );
}

function StepBadge({ step, members }: { step: CatalogStep; members: number }) {
  return (
    <>
      {step.kind === "collect" ? (
        <span className="rounded-full bg-brand-tint px-2 py-0.5 text-[11px] font-medium text-brand">
          Documents from candidate
        </span>
      ) : null}
      {step.kind === "system" ? (
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
          {step.system_action === "bgv" ? "Background check" : "Policy sign-off"}
        </span>
      ) : null}
      {members > 1 ? (
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
          Sent together
        </span>
      ) : null}
    </>
  );
}

function describe(lead: CatalogStep, group: CatalogStep[]): string {
  if (lead.kind === "collect") {
    const n = lead.items.length;
    return n
      ? `${n} document${n === 1 ? "" : "s"}: ${lead.items.map((i) => i.label).join(", ")}`
      : "No documents on the checklist yet.";
  }
  if (lead.kind === "generate" && group.length > 1) {
    return `Sends ${group.map((s) => s.label).join(" and ")} as one document.`;
  }
  return lead.description ?? "";
}

/** Move a step one place up or down. */
function Reorder({
  disabled,
  isFirst,
  isLast,
  onMove,
}: {
  disabled: boolean;
  isFirst: boolean;
  isLast: boolean;
  onMove: (dir: "up" | "down") => void;
}) {
  return (
    <div className="mt-0.5 flex flex-col">
      <button
        type="button"
        aria-label="Move step earlier"
        disabled={disabled || isFirst}
        onClick={() => onMove("up")}
        className="text-muted-foreground transition-colors hover:text-brand disabled:opacity-25 disabled:hover:text-muted-foreground"
      >
        <ChevronUp className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        aria-label="Move step later"
        disabled={disabled || isLast}
        onClick={() => onMove("down")}
        className="text-muted-foreground transition-colors hover:text-brand disabled:opacity-25 disabled:hover:text-muted-foreground"
      >
        <ChevronDown className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/**
 * Who signs, in the order they sign.
 *
 * Selection order is routing order, so picking HR then Candidate means HR
 * signs first — which is why this appends rather than sorting into a fixed
 * sequence. No selection means the document is sent, not signed.
 */
function SignerPicker({
  roles,
  disabled,
  onChange,
}: {
  roles: SignerRole[];
  disabled: boolean;
  onChange: (roles: SignerRole[]) => void;
}) {
  const toggle = (role: SignerRole) =>
    onChange(
      roles.includes(role) ? roles.filter((r) => r !== role) : [...roles, role],
    );

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] text-muted-foreground">Signed by:</span>
      {(Object.keys(SIGNER_LABELS) as SignerRole[]).map((role) => {
        const at = roles.indexOf(role);
        return (
          <button
            key={role}
            type="button"
            disabled={disabled}
            onClick={() => toggle(role)}
            className={cn(
              "rounded-full border px-2 py-0.5 text-[11px] transition-colors disabled:opacity-50",
              at >= 0
                ? "border-brand bg-brand-tint text-brand"
                : "border-border text-muted-foreground hover:border-brand hover:text-brand",
            )}
          >
            {SIGNER_LABELS[role]}
            {at >= 0 && roles.length > 1 ? ` (${at + 1})` : ""}
          </button>
        );
      })}
      {roles.length === 0 ? (
        <span className="text-[11px] text-muted-foreground">
          nobody — sent as-is
        </span>
      ) : null}
    </div>
  );
}

/** Pick what kind of step to add, then fill in the details for that kind. */
function AddStep({
  steps,
  documentTypes,
  onSave,
  onCancel,
  saving,
}: {
  steps: CatalogStep[];
  documentTypes: DocumentType[];
  onSave: (step: NewStep) => Promise<void>;
  onCancel: () => void;
  saving: boolean;
}) {
  const [chosen, setChosen] = useState<(typeof STEP_TYPES)[number] | null>(null);

  if (!chosen) {
    return (
      <div className="rounded-xl border border-brand bg-brand-tint/20 p-4">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">
            What happens at this step?
          </p>
          <button type="button" onClick={onCancel} aria-label="Cancel">
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {STEP_TYPES.map((type) => (
            <button
              key={type.id}
              type="button"
              onClick={() => setChosen(type)}
              className="flex items-start gap-3 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-brand"
            >
              <type.Icon className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">
                  {type.title}
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">
                  {type.blurb}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <StepForm
      type={chosen}
      steps={steps}
      documentTypes={documentTypes}
      saving={saving}
      onBack={() => setChosen(null)}
      onCancel={onCancel}
      onSave={onSave}
    />
  );
}

function StepForm({
  type,
  steps,
  documentTypes,
  onSave,
  onBack,
  onCancel,
  saving,
}: {
  type: (typeof STEP_TYPES)[number];
  steps: CatalogStep[];
  documentTypes: DocumentType[];
  onSave: (step: NewStep) => Promise<void>;
  onBack: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const [label, setLabel] = useState(type.defaultLabel);
  const [after, setAfter] = useState<string>(
    steps.length ? steps[steps.length - 1].step_key : "",
  );
  const [items, setItems] = useState<DraftItem[]>([newItem("10th marksheet")]);
  const [docKeys, setDocKeys] = useState<string[]>([]);
  const [signers, setSigners] = useState<SignerRole[]>([]);

  const valid =
    label.trim().length > 0 &&
    (type.kind !== "collect" || items.some((i) => i.label.trim())) &&
    (type.kind !== "generate" || docKeys.length > 0);

  return (
    <div className="rounded-xl border border-brand bg-brand-tint/20 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <type.Icon className="h-4 w-4 text-brand" />
          <p className="text-sm font-semibold text-foreground">{type.title}</p>
        </div>
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
            placeholder={type.defaultLabel}
            className="mt-1"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-foreground">Runs after</span>
          <select
            value={after}
            onChange={(e) => setAfter(e.target.value)}
            className="mt-1 h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
          >
            <option value="">Nothing — this goes first</option>
            {steps.map((s) => (
              <option key={s.step_key} value={s.step_key}>
                {s.bundle_label ?? s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {type.kind === "collect" ? (
        <ChecklistFields items={items} onChange={setItems} />
      ) : null}

      {type.kind === "generate" ? (
        <div className="mt-3 space-y-3">
          <div>
            <p className="text-xs font-medium text-foreground">
              Which documents?
            </p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Pick more than one and they go out together, in a single signing
              request.
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {documentTypes.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No document templates yet — add one under Document templates
                  first.
                </p>
              ) : null}
              {documentTypes.map((dt) => {
                const at = docKeys.indexOf(dt.key);
                return (
                  <button
                    key={dt.key}
                    type="button"
                    onClick={() =>
                      setDocKeys(
                        at >= 0
                          ? docKeys.filter((k) => k !== dt.key)
                          : [...docKeys, dt.key],
                      )
                    }
                    title={
                      dt.has_template
                        ? dt.description ?? dt.label
                        : "No template uploaded yet — the run will pause here until there is one."
                    }
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs transition-colors",
                      at >= 0
                        ? "border-brand bg-brand-tint text-brand"
                        : "border-border text-muted-foreground hover:border-brand hover:text-brand",
                    )}
                  >
                    {dt.label}
                    {dt.has_template ? "" : " ·  no template"}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <p className="text-xs font-medium text-foreground">Who signs it?</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              In the order you pick them. Choose nobody to send it unsigned.
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(Object.keys(SIGNER_LABELS) as SignerRole[]).map((role) => {
                const at = signers.indexOf(role);
                return (
                  <button
                    key={role}
                    type="button"
                    onClick={() =>
                      setSigners(
                        at >= 0
                          ? signers.filter((r) => r !== role)
                          : [...signers, role],
                      )
                    }
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs transition-colors",
                      at >= 0
                        ? "border-brand bg-brand-tint text-brand"
                        : "border-border text-muted-foreground hover:border-brand hover:text-brand",
                    )}
                  >
                    {SIGNER_LABELS[role]}
                    {at >= 0 && signers.length > 1 ? ` (${at + 1})` : ""}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex justify-between gap-2">
        <Button variant="ghost" onClick={onBack} disabled={saving}>
          Back
        </Button>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
          <Button
            disabled={!valid || saving}
            onClick={() =>
              onSave({
                kind: type.kind,
                label: label.trim(),
                after_step_key: after || null,
                items:
                  type.kind === "collect"
                    ? items.filter((i) => i.label.trim())
                    : undefined,
                document_type_keys:
                  type.kind === "generate" ? docKeys : undefined,
                signer_roles: type.kind === "generate" ? signers : undefined,
                system_action: type.system_action,
              })
            }
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Add step
          </Button>
        </div>
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
