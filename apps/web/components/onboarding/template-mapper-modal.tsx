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

interface MappingItem {
  blank_text: string;
  variable: string;
  context_before: string;
  context_after: string;
  confidence: "high" | "medium" | "low";
  // Exact position of the blank in the canonical paragraph enumeration. The
  // UI treats these as opaque and round-trips them unchanged to apply-mappings
  // (same pattern as TextBlock.index) — the backend substitutes by position.
  paragraph_index: number;
  start_offset: number;
  end_offset: number;
}

interface AnalyzeResponse {
  document_id: string;
  template_kind: string;
  has_placeholders: boolean;
  mappings: MappingItem[];
  text_preview: string;
  available_variables: AvailableVariable[];
  warning: string | null;
}

interface ApplyResponse {
  document_id: string;
  template_kind: string;
  applied_count: number;
  skipped_count?: number;
  preview_url: string | null;
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
  onApplied?: (response: ApplyResponse) => void;
}

type Stage =
  | "analyzing"
  | "applying"
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

const CONFIDENCE_STYLE: Record<MappingItem["confidence"], string> = {
  high: "bg-success-tint text-success",
  medium: "bg-amber-tint text-amber",
  low: "bg-muted text-muted-foreground",
};

// Vertical stepper shown while the analyzer runs. Order matches the async
// pipeline in the effect below: analyze → apply-mappings → load blocks.
const STEPS: { key: Stage; label: string }[] = [
  { key: "analyzing", label: "Find blanks & placeholders" },
  { key: "applying", label: "Convert blanks into placeholders" },
  { key: "loading", label: "Load the template for review" },
];

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
  const [showMappings, setShowMappings] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const onAppliedRef = useRef(onApplied);
  onAppliedRef.current = onApplied;

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

  async function previewPdf() {
    const origin = stage;
    setStage("previewing");
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
      setShowMappings(false);
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

      if (analyzed.mappings.length > 0) {
        setStage("applying");
        const applyRes = await fetch(
          `/api/onboarding/templates/${documentId}/apply-mappings`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mappings: analyzed.mappings }),
          },
        );
        const applyBody = await readJson(applyRes);
        if (cancelled) return;
        if (!applyRes.ok) {
          setError(
            errorText(
              applyBody,
              "Couldn't apply the mappings. The DOCX may have formatting that's hard to parse.",
            ),
          );
          setStage("error");
          return;
        }
        const appliedResp = applyBody as ApplyResponse;
        onAppliedRef.current?.(appliedResp);
      }

      // Load the template as editable text (placeholders visible). HR reviews
      // and edits the wording, then hits Preview PDF to see the rendered doc.
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
  const mappings = analysis?.mappings ?? [];
  const stageLabel: Record<Stage, string> = {
    analyzing: "Reading template and finding blanks…",
    applying: `Applying ${mappings.length} mapping${mappings.length === 1 ? "" : "s"}…`,
    loading: "Loading editable text…",
    editing: "Template ready to review",
    previewing: "Saving edits and rendering preview…",
    preview: "Preview",
    saving: "Saving template…",
    saved: "Template saved",
    error: "Something went wrong",
  };

  const inProgress =
    stage === "analyzing" || stage === "applying" || stage === "loading";
  const isEditing =
    stage === "editing" || stage === "previewing" || stage === "saving";
  const busy = stage === "previewing" || stage === "saving";

  const currentStepIdx = STEPS.findIndex((s) => s.key === stage);
  const headerTitle =
    stage === "saved"
      ? "Template saved"
      : stage === "error"
        ? "Couldn't check template"
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="rounded-2xl sm:max-w-4xl">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <span
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl",
                headerChip,
              )}
            >
              <HeaderIcon className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0 flex-1">
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
          <ol className="py-1">
            {STEPS.map((s, i) => {
              const done = i < currentStepIdx;
              const active = i === currentStepIdx;
              const last = i === STEPS.length - 1;
              return (
                <li key={s.key} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <span
                      className={cn(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold transition-colors",
                        done
                          ? "bg-success text-white"
                          : active
                            ? "bg-brand text-brand-foreground"
                            : "bg-muted text-muted-foreground",
                      )}
                    >
                      {done ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : active ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        i + 1
                      )}
                    </span>
                    {!last ? (
                      <span
                        className={cn(
                          "my-1 w-0.5 flex-1 rounded-full",
                          done ? "bg-success" : "bg-border",
                        )}
                      />
                    ) : null}
                  </div>
                  <div className={cn("min-w-0", last ? "pb-0" : "pb-5")}>
                    <p
                      className={cn(
                        "text-sm font-bold",
                        active || done
                          ? "text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {s.label}
                    </p>
                    {active ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">
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
        {isEditing && analysis?.warning ? (
          <div className="flex items-start gap-2 rounded-2xl border border-amber/30 bg-amber-tint p-3 text-xs font-medium text-amber-ink">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <p>{analysis.warning}</p>
          </div>
        ) : null}

        {/* Editing — editable text */}
        {isEditing ? (
          <div className="space-y-3">
            {mappings.length > 0 ? (
              <div className="rounded-xl border border-border bg-muted/40 p-3">
                <button
                  type="button"
                  onClick={() => setShowMappings((v) => !v)}
                  className="text-xs font-bold text-brand transition-colors hover:text-foreground"
                >
                  {showMappings ? "Hide" : "Show"} {mappings.length} auto-mapping
                  {mappings.length === 1 ? "" : "s"}
                </button>
                {showMappings ? (
                  <ul className="mt-2 max-h-36 space-y-1.5 overflow-y-auto pr-1">
                    {mappings.map((m, i) => (
                      <li
                        key={`${m.blank_text}-${i}`}
                        className="flex items-center gap-2 text-[11px]"
                      >
                        <code className="truncate rounded-md bg-card px-1.5 py-0.5 font-mono text-foreground ring-1 ring-border">
                          {m.blank_text}
                        </code>
                        <span className="shrink-0 text-muted-foreground">→</span>
                        <code className="truncate rounded-md bg-card px-1.5 py-0.5 font-mono text-brand ring-1 ring-border">
                          {`{{ ${m.variable} }}`}
                        </code>
                        <span
                          className={cn(
                            "ml-auto inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold",
                            CONFIDENCE_STYLE[m.confidence],
                          )}
                        >
                          <span className="h-1 w-1 rounded-full bg-current" />
                          {m.confidence}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

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
          {isEditing ? (
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
