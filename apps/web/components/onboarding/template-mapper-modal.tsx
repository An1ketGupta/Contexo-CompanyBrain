"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ExternalLink,
  Eye,
  Loader2,
  Sparkles,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface AvailableVariable {
  name: string;
  label: string;
  description: string;
}

/** One persisted fill-point in a `slots` template.
 *
 *  `action` matters for rendering: only `replace_span` has visible blank text.
 *  The two `insert_*` actions mark a spot that is currently EMPTY (a label with
 *  nothing after it, an empty table cell), so they must be shown by their
 *  surrounding context — there is no blank text to display. */
interface SlotItem {
  id: string;
  action: "replace_span" | "insert_after_label" | "insert_empty_cell";
  status: "proposed" | "confirmed" | "rejected";
  source: "ai" | "manual";
  variable: string | null;
  confidence: "high" | "medium" | "low";
  blank_text: string;
  context_before: string;
  context_after: string;
  paragraph_index: number;
  paragraph_kind: string;
  start_offset: number;
  end_offset: number;
}

interface AnalyzeResponse {
  document_id: string;
  template_kind: string;
  has_placeholders: boolean;
  /** Which fill pipeline this template uses. `slots` — an ordinary HR document,
   *  runs the per-field review flow below. `jinja` — hand-authored with valid
   *  `{{ }}` tags, rendered by docxtpl and needing no mapping, so it skips
   *  straight to the wording editor. */
  fill_strategy: "slots" | "jinja";
  slots: SlotItem[];
  text_preview: string;
  available_variables: AvailableVariable[];
  warning: string | null;
  /** Problems in hand-typed `{{ }}` tags, in plain English. A tag that can't
   *  parse is reported here instead of failing later inside the renderer. */
  jinja_errors: string[];
  unknown_variables: string[];
}

interface SlotsResponse {
  document_id: string;
  template_kind: string;
  fill_strategy: string | null;
  slots: SlotItem[];
  available_variables: AvailableVariable[];
  pending_count: number;
}

interface RenderPreviewResponse {
  document_id: string;
  template_kind: string;
  preview_url: string | null;
  filled_count: number;
  pending_count: number;
  /** Spots in the RENDERED output that still look unfilled — the safety net for
   *  a fill-point no heuristic could find. Non-fatal. */
  unfilled_warnings: string[];
  preview_error: string | null;
}

interface TextBlock {
  index: number;
  text: string;
  kind: string;
}

interface BlocksResponse {
  document_id: string;
  template_kind: string;
  blocks: TextBlock[];
}

interface EditTextResponse {
  document_id: string;
  template_kind: string;
  changed_count: number;
  preview_url: string | null;
  preview_error: string | null;
}

interface TemplateMapperModalProps {
  documentId: string | null;
  documentName?: string;
  templateKind: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fired once the template has been promoted to active, so the caller can
   *  refresh whatever it shows about template status. */
  onApplied?: () => void;
}

type Stage =
  | "analyzing"
  // Slots templates only: HR confirms or dismisses each detected field before
  // anything is rendered. The pre-097 flow had no equivalent — it auto-applied
  // every AI proposal and only showed a read-only list afterwards.
  | "reviewing"
  | "loading"
  | "editing"
  | "previewing"
  | "preview"
  | "saving"
  | "saved"
  | "error";

const KIND_LABEL: Record<string, string> = {
  loi: "Letter of Intent",
  appointment_letter: "Appointment Letter",
  nda: "NDA",
  induction: "Induction",
};

const BLOCK_KIND_LABEL: Record<string, string> = {
  header: "Header",
  footer: "Footer",
  table: "Table",
};

const CONFIDENCE_STYLE: Record<SlotItem["confidence"], string> = {
  high: "bg-success-tint text-success",
  medium: "bg-amber-tint text-amber",
  low: "bg-muted text-muted-foreground",
};

// Vertical stepper shown while the analyzer runs. Order matches the async
// pipeline in the effect below: analyze → load blocks.
const STEPS: { key: Stage; label: string }[] = [
  { key: "analyzing", label: "Find the fields in this template" },
  { key: "loading", label: "Load the template for review" },
];

const ACTION_LABEL: Record<SlotItem["action"], string> = {
  replace_span: "Blank",
  insert_after_label: "After label",
  insert_empty_cell: "Empty cell",
};

/** What HR reads to decide which value belongs in a fill-point.
 *
 *  For `replace_span` the blank itself is shown highlighted in its sentence.
 *  For the insert actions there is nothing to highlight — the spot is empty —
 *  so we show the preceding label text and mark the insertion point. */
function SlotSnippet({ slot }: { slot: SlotItem }) {
  const before = slot.context_before.replace(/\s+/g, " ").trimStart();
  const after = slot.context_after.replace(/\s+/g, " ").trimEnd();
  return (
    <p className="truncate text-[11px] leading-5 text-muted-foreground">
      {before ? <span>…{before}</span> : null}
      {slot.action === "replace_span" ? (
        <code className="mx-1 rounded bg-amber-tint px-1 py-0.5 font-mono text-[10px] text-amber-ink">
          {slot.blank_text || " "}
        </code>
      ) : (
        <span className="mx-1 rounded bg-violet-tint px-1 py-0.5 font-mono text-[10px] text-violet">
          ⌷ value goes here
        </span>
      )}
      {after ? <span>{after}…</span> : null}
    </p>
  );
}

async function readJson<T = unknown>(res: Response): Promise<T> {
  return (await res.json().catch(() => ({}))) as T;
}

/** Append PDF-viewer fragment params so the browser's built-in dark toolbar,
 *  side panels, and page chrome are hidden — leaving just the rendered page.
 *  The fragment goes after any existing query string on the signed URL. */
function toChromelessPdf(url: string): string {
  const [base, existingHash] = url.split("#");
  const params = "toolbar=0&navpanes=0&statusbar=0&view=FitH";
  return existingHash ? `${base}#${existingHash}&${params}` : `${base}#${params}`;
}

function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const b = body as { detail?: unknown; message?: string };
    if (typeof b.detail === "string") return b.detail;
    if (typeof b.message === "string") return b.message;
  }
  return fallback;
}

/** A textarea that grows to fit its content — HR edits template lines that
 *  range from one word to a full clause. */
function AutoTextarea({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
      className="w-full resize-none overflow-hidden rounded-sm border-0 bg-transparent px-1 py-0.5 text-[15px] leading-7 text-neutral-900 outline-none focus:bg-primary/5 disabled:opacity-60"
    />
  );
}

export function TemplateMapperModal({
  documentId,
  documentName,
  templateKind,
  open,
  onOpenChange,
  onApplied,
}: TemplateMapperModalProps) {
  const [stage, setStage] = useState<Stage>("analyzing");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [blocks, setBlocks] = useState<TextBlock[]>([]);
  const [edited, setEdited] = useState<Record<number, string>>({});
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Per-field review state.
  const [slots, setSlots] = useState<SlotItem[]>([]);
  // Variable currently selected per slot, before HR confirms it. Seeded from
  // the classifier's guess so the common case is one click, not a dropdown hunt.
  const [slotVar, setSlotVar] = useState<Record<string, string>>({});
  const [savingSlotId, setSavingSlotId] = useState<string | null>(null);
  const [unfilledWarnings, setUnfilledWarnings] = useState<string[]>([]);
  const onAppliedRef = useRef(onApplied);
  onAppliedRef.current = onApplied;

  const isSlots = analysis?.fill_strategy === "slots";
  const activeSlots = slots.filter((s) => s.status !== "rejected");
  const pendingSlots = slots.filter((s) => s.status === "proposed");

  const currentText = (b: TextBlock) =>
    edited[b.index] !== undefined ? edited[b.index] : b.text;
  const dirty = blocks.some(
    (b) => edited[b.index] !== undefined && edited[b.index] !== b.text,
  );

  /** Write the current edits back to the DOCX and render a fresh preview.
   *  Returns the response (or null on a hard error) and resets the edit
   *  baseline so `dirty` clears on success. */
  async function persistEdits(): Promise<EditTextResponse | null> {
    if (!documentId) return null;
    setActionError(null);
    const merged = blocks.map((b) => ({
      index: b.index,
      text: currentText(b),
      kind: b.kind,
    }));
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/edit-text`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edits: merged }),
      },
    );
    const body = await readJson(res);
    if (!res.ok) {
      setActionError(errorText(body, "Couldn't save your edits."));
      return null;
    }
    const resp = body as EditTextResponse;
    setBlocks(merged);
    setEdited({});
    if (resp.preview_url) setPreviewUrl(resp.preview_url);
    if (resp.preview_error) setActionError(resp.preview_error);
    return resp;
  }

  /** Record HR's verdict on one field. Fires immediately rather than batching:
   *  a half-finished review that survives a refresh is better than one lost. */
  async function decideSlot(slot: SlotItem, status: "confirmed" | "rejected") {
    if (!documentId) return;
    const variable = slotVar[slot.id] ?? slot.variable ?? "";
    if (status === "confirmed" && !variable) {
      setActionError("Pick a value for this field before confirming it.");
      return;
    }
    setActionError(null);
    setSavingSlotId(slot.id);
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/slots/${slot.id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status,
          variable: status === "confirmed" ? variable : null,
        }),
      },
    );
    setSavingSlotId(null);
    if (!res.ok) {
      setActionError(
        errorText(await readJson(res), "Couldn't save your decision."),
      );
      return;
    }
    setSlots((prev) =>
      prev.map((s) =>
        s.id === slot.id
          ? { ...s, status, variable: status === "confirmed" ? variable : s.variable }
          : s,
      ),
    );
  }

  /** Re-run detection. Safe by construction — the backend only refreshes
   *  still-proposed fields and never overwrites a decision HR already made. */
  async function refindFields() {
    if (!documentId) return;
    setActionError(null);
    setStage("analyzing");
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/analyze`,
      { method: "POST" },
    );
    const body = await readJson(res);
    if (!res.ok) {
      setError(errorText(body, "Couldn't analyze the template."));
      setStage("error");
      return;
    }
    const analyzed = body as AnalyzeResponse;
    setAnalysis(analyzed);
    setSlots(analyzed.slots ?? []);
    seedSlotVars(analyzed.slots ?? []);
    setStage("reviewing");
  }

  function seedSlotVars(items: SlotItem[]) {
    setSlotVar(
      Object.fromEntries(
        items.filter((s) => s.variable).map((s) => [s.id, s.variable as string]),
      ),
    );
  }

  /** Load the flat-text view HR uses to reword the template. */
  async function loadBlocks(): Promise<boolean> {
    if (!documentId) return false;
    setStage("loading");
    const res = await fetch(`/api/onboarding/templates/${documentId}/blocks`);
    const body = await readJson(res);
    if (!res.ok) {
      setError(errorText(body, "Couldn't read the template text."));
      setStage("error");
      return false;
    }
    setBlocks((body as BlocksResponse).blocks ?? []);
    setEdited({});
    setStage("editing");
    return true;
  }

  /** Slots preview: render the confirmed fields against sample data.
   *  Never mutates the stored template — unlike the legacy apply+dry-render. */
  async function renderSlotsPreview(): Promise<boolean> {
    if (!documentId) return false;
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/render-preview`,
      { method: "POST" },
    );
    const body = await readJson(res);
    if (!res.ok) {
      setActionError(errorText(body, "Couldn't render a preview."));
      return false;
    }
    const resp = body as RenderPreviewResponse;
    setUnfilledWarnings(resp.unfilled_warnings ?? []);
    if (resp.preview_error) setActionError(resp.preview_error);
    if (resp.preview_url) {
      setPreviewUrl(resp.preview_url);
      return true;
    }
    return false;
  }

  async function previewPdf() {
    const origin = stage;
    setStage("previewing");
    if (isSlots) {
      // Persist any wording edits first so the preview reflects them, then fill
      // from the confirmed fields. If HR edited a mapped paragraph, the backend
      // reports drift here rather than at generation time.
      if (dirty) {
        const edits = await persistEdits();
        if (!edits) {
          setStage(origin === "preview" ? "preview" : "editing");
          return;
        }
      }
      const ok = await renderSlotsPreview();
      setStage(ok ? "preview" : origin === "preview" ? "preview" : "editing");
      return;
    }
    const resp = await persistEdits();
    if (resp && resp.preview_url) {
      setStage("preview");
    } else {
      setStage(origin === "preview" ? "preview" : "editing");
    }
  }

  async function saveTemplate() {
    if (!documentId) return;
    const origin: Stage = stage === "preview" ? "preview" : "editing";
    setActionError(null);
    setStage("saving");
    if (dirty) {
      const resp = await persistEdits();
      if (!resp) {
        setStage(origin);
        return;
      }
    }
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/save`,
      { method: "POST" },
    );
    if (!res.ok) {
      setActionError(
        errorText(await readJson(res), "Couldn't save the template."),
      );
      setStage(origin);
      return;
    }
    setStage("saved");
    onAppliedRef.current?.();
  }

  useEffect(() => {
    if (!open || !documentId) {
      setStage("analyzing");
      setAnalysis(null);
      setBlocks([]);
      setEdited({});
      setPreviewUrl(null);
      setError(null);
      setActionError(null);
      setSlots([]);
      setSlotVar({});
      setSavingSlotId(null);
      setUnfilledWarnings([]);
      return;
    }

    let cancelled = false;
    void (async () => {
      setStage("analyzing");
      setError(null);

      const analyzeRes = await fetch(
        `/api/onboarding/templates/${documentId}/analyze`,
        { method: "POST" },
      );
      const analyzeBody = await readJson(analyzeRes);
      if (cancelled) return;
      if (!analyzeRes.ok) {
        setError(errorText(analyzeBody, "Couldn't analyze the template."));
        setStage("error");
        return;
      }
      const analyzed = analyzeBody as AnalyzeResponse;
      setAnalysis(analyzed);

      // Slots template: stop here and let HR review every detected field.
      // Nothing has been written to the document and nothing will be rendered
      // until they confirm — which is the whole point of this pipeline.
      if (analyzed.fill_strategy === "slots") {
        const found = analyzed.slots ?? [];
        setSlots(found);
        setSlotVar(
          Object.fromEntries(
            found
              .filter((s) => s.variable)
              .map((s) => [s.id, s.variable as string]),
          ),
        );
        // Nothing left to decide (a re-open after review, or a template with no
        // detectable fields) — go straight to the text view.
        if (found.some((s) => s.status === "proposed")) {
          setStage("reviewing");
          return;
        }
      }

      // Either a hand-authored `{{ }}` template (nothing to map), or a slots
      // template whose fields are all decided. Load it as editable text so HR
      // reviews the wording, then hits Preview PDF to see the rendered doc.
      setStage("loading");
      const blocksRes = await fetch(
        `/api/onboarding/templates/${documentId}/blocks`,
      );
      const blocksBody = await readJson(blocksRes);
      if (cancelled) return;
      if (!blocksRes.ok) {
        setError(errorText(blocksBody, "Couldn't read the template text."));
        setStage("error");
        return;
      }
      setBlocks((blocksBody as BlocksResponse).blocks ?? []);
      setEdited({});
      setStage("editing");
    })();

    return () => {
      cancelled = true;
    };
  }, [open, documentId]);

  const kindLabel = KIND_LABEL[templateKind] || templateKind;
  const stageLabel: Record<Stage, string> = {
    analyzing: "Reading template and finding fields…",
    reviewing: "Confirm what goes in each field",
    loading: "Loading editable text…",
    editing: "Template ready to review",
    previewing: "Saving edits and rendering preview…",
    preview: "Preview",
    saving: "Saving template…",
    saved: "Template saved",
    error: "Something went wrong",
  };

  const inProgress = stage === "analyzing" || stage === "loading";
  const isEditing =
    stage === "editing" || stage === "previewing" || stage === "saving";
  const busy = stage === "previewing" || stage === "saving";

  const currentStepIdx = STEPS.findIndex((s) => s.key === stage);
  const headerTitle =
    stage === "saved"
      ? "Template saved"
      : stage === "error"
        ? "Couldn't check template"
        : stage === "reviewing"
          ? "Confirm the fields we found"
          : isEditing || stage === "preview"
            ? "Review and save"
            : "Checking template";
  const HeaderIcon =
    stage === "saved"
      ? CheckCircle2
      : stage === "error"
        ? AlertTriangle
        : Sparkles;
  const headerChip =
    stage === "saved"
      ? "bg-success-tint text-success"
      : stage === "error"
        ? "bg-destructive-soft text-destructive"
        : "bg-violet-tint text-violet";

  // The editing / preview / review stages render document text, a field list, or
  // a PDF, so they need the wide canvas. Progress, saved and error states are
  // short — a narrower dialog keeps them centered instead of floating in a
  // mostly-empty box.
  const wide = isEditing || stage === "preview" || stage === "reviewing";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn("rounded-2xl", wide ? "sm:max-w-4xl" : "sm:max-w-lg")}
      >
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1 pt-0.5">
              <DialogTitle>{headerTitle}</DialogTitle>
              <DialogDescription className="mt-1">
                {documentName ? (
                  <>
                    <span className="font-semibold text-foreground">
                      {documentName}
                    </span>
                    {" · "}
                  </>
                ) : null}
                {kindLabel}
                {stage === "saved" ? (
                  <> · this template is now live. The Onboarding agent will use it for every new candidate.</>
                ) : stage === "preview" ? (
                  <> · read-only preview. Go back to editing to make changes, or Save to publish.</>
                ) : ""}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {/* Progress — vertical stepper */}
        {inProgress ? (
          <ol className="rounded-2xl border border-border bg-muted/30 p-4 pr-5">
            {STEPS.map((s, i) => {
              const done = i < currentStepIdx;
              const active = i === currentStepIdx;
              const last = i === STEPS.length - 1;
              return (
                <li key={s.key} className="flex gap-3.5">
                  <div className="flex flex-col items-center">
                    <span
                      className={cn(
                        "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold transition-all",
                        done
                          ? "bg-success text-white"
                          : active
                            ? "bg-brand text-brand-foreground ring-4 ring-brand/15"
                            : "bg-muted text-muted-foreground ring-1 ring-inset ring-border",
                      )}
                    >
                      {done ? (
                        <Check className="h-4 w-4" />
                      ) : active ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        i + 1
                      )}
                    </span>
                    {!last ? (
                      <span
                        className={cn(
                          "my-1.5 w-px flex-1 rounded-full transition-colors",
                          done ? "bg-success" : "bg-border",
                        )}
                      />
                    ) : null}
                  </div>
                  <div className={cn("min-w-0 pt-0.5", last ? "pb-0.5" : "pb-5")}>
                    <p
                      className={cn(
                        "text-sm font-semibold transition-colors",
                        active || done
                          ? "text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {s.label}
                    </p>
                    {active ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {stageLabel[stage]}
                      </p>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>
        ) : null}

        {/* Error */}
        {stage === "error" && error ? (
          <div className="flex items-start gap-3 rounded-2xl border border-destructive/30 bg-destructive-soft p-4 text-sm font-medium text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <p>{error}</p>
          </div>
        ) : null}

        {/* Saved confirmation */}
        {stage === "saved" ? (
          <div className="flex items-center gap-3 rounded-2xl border border-success/30 bg-success-tint p-4">
            <CheckCircle2 className="h-5 w-5 shrink-0 text-success" />
            <p className="text-sm font-medium text-success-ink">
              Placeholders are wired up and the template is published.
            </p>
          </div>
        ) : null}

        {/* AI warning (analyzer failed but we kept going) */}
        {(isEditing || stage === "reviewing") && analysis?.warning ? (
          <div className="flex items-start gap-2 rounded-2xl border border-amber/30 bg-amber-tint p-3 text-xs font-medium text-amber-ink">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <p>{analysis.warning}</p>
          </div>
        ) : null}

        {/* Malformed hand-typed placeholders. Reported here, at analyze time,
            because this is precisely the failure that used to surface as an
            opaque parser error mid-render with no indication of the culprit. */}
        {(stage === "reviewing" || isEditing) && analysis?.jinja_errors?.length ? (
          <div className="rounded-2xl border border-amber/30 bg-amber-tint p-3 text-xs text-amber-ink">
            <p className="font-bold">Placeholders that aren&apos;t valid</p>
            <ul className="mt-1.5 space-y-1">
              {analysis.jinja_errors.slice(0, 5).map((e, i) => (
                <li key={i} className="leading-5">
                  {e}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 leading-5">
              They&apos;re treated as ordinary blanks below, so you can map them
              here instead of editing the Word file.
            </p>
          </div>
        ) : null}

        {/* Per-field review — the human-in-the-loop gate. */}
        {stage === "reviewing" ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground">
                {pendingSlots.length > 0 ? (
                  <>
                    <span className="font-bold text-foreground">
                      {pendingSlots.length}
                    </span>{" "}
                    field{pendingSlots.length === 1 ? "" : "s"} need a decision.
                    Nothing is filled in until you confirm it.
                  </>
                ) : (
                  <>All fields reviewed. Continue to check the wording.</>
                )}
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-xs"
                onClick={refindFields}
              >
                Find fields again
              </Button>
            </div>

            {slots.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border bg-muted/40 p-5 text-center">
                <p className="text-sm font-medium text-foreground">
                  No fields detected
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  This template may have blanks we can&apos;t recognise. Continue
                  to the text view — you can still edit the wording, and the
                  preview will flag anything left unfilled.
                </p>
              </div>
            ) : (
              <ul className="max-h-[50vh] space-y-2 overflow-y-auto pr-1">
                {slots.map((slot) => {
                  const decided = slot.status !== "proposed";
                  const rejected = slot.status === "rejected";
                  const selected = slotVar[slot.id] ?? slot.variable ?? "";
                  const saving = savingSlotId === slot.id;
                  return (
                    <li
                      key={slot.id}
                      className={cn(
                        "rounded-xl border p-3 transition-colors",
                        rejected
                          ? "border-border bg-muted/30 opacity-60"
                          : slot.status === "confirmed"
                            ? "border-success/30 bg-success-tint/40"
                            : "border-border bg-card",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                          {ACTION_LABEL[slot.action]}
                        </span>
                        {slot.source === "manual" ? (
                          <span className="rounded-full bg-violet-tint px-2 py-0.5 text-[10px] font-bold text-violet">
                            added by you
                          </span>
                        ) : (
                          <span
                            className={cn(
                              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold",
                              CONFIDENCE_STYLE[slot.confidence],
                            )}
                          >
                            <span className="h-1 w-1 rounded-full bg-current" />
                            {slot.confidence}
                          </span>
                        )}
                        {slot.status === "confirmed" ? (
                          <span className="ml-auto inline-flex items-center gap-1 text-[10px] font-bold text-success">
                            <Check className="h-3 w-3" /> confirmed
                          </span>
                        ) : rejected ? (
                          <span className="ml-auto text-[10px] font-bold text-muted-foreground">
                            dismissed
                          </span>
                        ) : null}
                      </div>

                      <div className="mt-1.5">
                        <SlotSnippet slot={slot} />
                      </div>

                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <select
                          value={selected}
                          disabled={saving || busy}
                          onChange={(e) =>
                            setSlotVar((prev) => ({
                              ...prev,
                              [slot.id]: e.target.value,
                            }))
                          }
                          className="min-w-0 flex-1 rounded-lg border border-border bg-card px-2 py-1.5 text-xs text-foreground outline-none focus:ring-2 focus:ring-brand/30 disabled:opacity-60"
                        >
                          <option value="">— pick a value —</option>
                          {(analysis?.available_variables ?? []).map((v) => (
                            <option key={v.name} value={v.name}>
                              {v.label}
                            </option>
                          ))}
                        </select>
                        <Button
                          size="sm"
                          variant={
                            slot.status === "confirmed" ? "outline" : "primary"
                          }
                          disabled={saving || busy}
                          onClick={() => decideSlot(slot, "confirmed")}
                        >
                          {saving ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : null}
                          {slot.status === "confirmed" ? "Update" : "Confirm"}
                        </Button>
                        {!rejected ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-xs text-muted-foreground"
                            disabled={saving || busy}
                            onClick={() => decideSlot(slot, "rejected")}
                          >
                            Dismiss
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-xs"
                            disabled={saving || busy}
                            onClick={() => decideSlot(slot, "confirmed")}
                          >
                            Restore
                          </Button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ) : null}

        {/* Post-render safety net — spots the OUTPUT still shows as unfilled. */}
        {unfilledWarnings.length > 0 && (isEditing || stage === "preview") ? (
          <div className="rounded-2xl border border-amber/30 bg-amber-tint p-3 text-xs text-amber-ink">
            <p className="font-bold">
              {unfilledWarnings.length} spot
              {unfilledWarnings.length === 1 ? "" : "s"} still look unfilled
            </p>
            <ul className="mt-1.5 max-h-24 space-y-1 overflow-y-auto">
              {unfilledWarnings.slice(0, 6).map((w, i) => (
                <li key={i} className="truncate leading-5">
                  {w}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 leading-5">
              If those should be filled in, add them as fields — otherwise ignore
              this.
            </p>
          </div>
        ) : null}

        {/* Editing — editable text */}
        {isEditing ? (
          <div className="space-y-3">
            {blocks.length > 0 ? (
              <div className="max-h-[52vh] overflow-y-auto rounded-2xl border border-border bg-white px-8 py-6 shadow-inner">
                <div className="mx-auto max-w-2xl space-y-1.5">
                  {blocks.map((b) => {
                    const badge = BLOCK_KIND_LABEL[b.kind];
                    return (
                      <div key={b.index} className="group relative">
                        {badge ? (
                          <span className="pointer-events-none absolute -left-7 top-1 hidden text-[9px] font-medium uppercase tracking-wide text-muted-foreground group-focus-within:inline">
                            {badge}
                          </span>
                        ) : null}
                        <AutoTextarea
                          value={currentText(b)}
                          onChange={(v) =>
                            setEdited((prev) => ({ ...prev, [b.index]: v }))
                          }
                          disabled={busy}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No editable text found in this template.
              </p>
            )}
          </div>
        ) : null}

        {/* Read-only PDF preview — the page floats like paper on a soft
            canvas, with the browser's native PDF chrome suppressed. */}
        {stage === "preview" ? (
          previewUrl ? (
            <div className="rounded-xl border border-border bg-muted p-3 shadow-inner sm:p-5">
              <div className="mx-auto max-w-3xl overflow-hidden rounded-lg bg-white shadow-[0_1px_2px_rgba(16,18,20,0.06),0_8px_24px_-8px_rgba(16,18,20,0.18)] ring-1 ring-black/5">
                <iframe
                  src={toChromelessPdf(previewUrl)}
                  title="Template preview (placeholders visible)"
                  className="h-[58vh] w-full bg-white"
                />
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-1 rounded-xl border border-dashed border-border bg-muted/40 py-10 text-center">
              <p className="text-sm font-medium text-foreground">
                Preview unavailable
              </p>
              <p className="text-xs text-muted-foreground">
                Go back to editing and try Preview PDF again.
              </p>
            </div>
          )
        ) : null}

        {/* Action-level errors (edit/save) — kept distinct from the
            initial analyze/apply errors which use the larger banner above. */}
        {actionError ? (
          <div className="flex items-start gap-2 rounded-2xl border border-amber/30 bg-amber-tint p-3 text-xs font-medium text-amber-ink">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <p>{actionError}</p>
          </div>
        ) : null}

        <DialogFooter className="gap-2">
          {stage === "reviewing" ? (
            <>
              <span className="mr-auto text-xs text-muted-foreground">
                {slots.filter((s) => s.status === "confirmed").length} confirmed
                {pendingSlots.length > 0
                  ? ` · ${pendingSlots.length} to go`
                  : ""}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={loadBlocks}
                disabled={pendingSlots.length > 0}
                title={
                  pendingSlots.length > 0
                    ? "Confirm or dismiss every field first — an unreviewed field won't be filled in."
                    : undefined
                }
              >
                Continue
              </Button>
            </>
          ) : isEditing ? (
            <>
              <Button
                variant="outline"
                size="sm"
                className="mr-auto"
                onClick={previewPdf}
                disabled={busy || blocks.length === 0}
              >
                {stage === "previewing" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Eye className="mr-1.5 h-3.5 w-3.5" />
                )}
                Preview PDF
              </Button>
              {isSlots ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setStage("reviewing")}
                  disabled={busy}
                >
                  {activeSlots.length} field
                  {activeSlots.length === 1 ? "" : "s"}
                </Button>
              ) : null}
              <Button size="sm" onClick={saveTemplate} disabled={busy}>
                {stage === "saving" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : null}
                Save template
              </Button>
            </>
          ) : stage === "preview" ? (
            <>
              <Button
                variant="outline"
                size="sm"
                className="mr-auto"
                onClick={() => setStage("editing")}
              >
                <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
                Back to editing
              </Button>
              {previewUrl ? (
                <a
                  href={previewUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
                >
                  Open in new tab <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
              <Button size="sm" onClick={saveTemplate}>
                Save template
              </Button>
            </>
          ) : stage === "saved" ? (
            <Button onClick={() => onOpenChange(false)}>Close</Button>
          ) : stage === "error" ? (
            <Button onClick={() => onOpenChange(false)}>Close</Button>
          ) : (
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={inProgress}
            >
              Cancel
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
