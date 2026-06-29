"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  Loader2,
  Sparkles,
  Upload,
} from "lucide-react";

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
  preview_url: string | null;
}

interface PreviewResponse {
  status: string;
  template_kind: string;
  preview_url: string;
  file_bytes: number;
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
  | "rendering"
  | "done"
  | "replacing"
  | "saving"
  | "saved"
  | "error";

const KIND_LABEL: Record<string, string> = {
  loi: "Letter of Intent",
  appointment_letter: "Appointment Letter",
  nda: "NDA",
  induction: "Induction",
};

const CONFIDENCE_STYLE: Record<MappingItem["confidence"], string> = {
  high: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/20 dark:text-emerald-200",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-200",
  low: "bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200",
};

async function readJson<T = unknown>(res: Response): Promise<T> {
  return (await res.json().catch(() => ({}))) as T;
}

function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const b = body as { detail?: unknown; message?: string };
    if (typeof b.detail === "string") return b.detail;
    if (typeof b.message === "string") return b.message;
  }
  return fallback;
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
  const [appliedCount, setAppliedCount] = useState(0);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showMappings, setShowMappings] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const replaceFileRef = useRef<HTMLInputElement>(null);
  const onAppliedRef = useRef(onApplied);
  onAppliedRef.current = onApplied;

  async function downloadDocx() {
    if (!documentId) return;
    setActionError(null);
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/docx-url`,
    );
    if (!res.ok) {
      setActionError("Couldn't get a download link. Try again.");
      return;
    }
    const body = (await res.json()) as { docx_url?: string };
    if (body.docx_url) window.open(body.docx_url, "_blank");
  }

  async function replaceDocx(file: File) {
    if (!documentId) return;
    setStage("replacing");
    setActionError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `/api/onboarding/templates/${documentId}/replace`,
        { method: "POST", body: form },
      );
      const body = (await readJson(res)) as {
        preview_url?: string;
        preview_error?: string | null;
      };
      if (!res.ok) {
        setActionError(
          errorText(body, "Couldn't upload the edited .docx."),
        );
        setStage("done");
        return;
      }
      if (body.preview_url) setPreviewUrl(body.preview_url);
      if (body.preview_error) setActionError(body.preview_error);
      setStage("done");
    } catch (exc) {
      setActionError(
        `Upload failed (${exc instanceof Error ? exc.message : String(exc)})`,
      );
      setStage("done");
    }
  }

  async function saveTemplate() {
    if (!documentId) return;
    setStage("saving");
    setActionError(null);
    const res = await fetch(
      `/api/onboarding/templates/${documentId}/save`,
      { method: "POST" },
    );
    if (!res.ok) {
      setActionError(
        errorText(await readJson(res), "Couldn't save the template."),
      );
      setStage("done");
      return;
    }
    setStage("saved");
  }

  useEffect(() => {
    if (!open || !documentId) {
      setStage("analyzing");
      setAnalysis(null);
      setAppliedCount(0);
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
        setAppliedCount(appliedResp.applied_count);
        onAppliedRef.current?.(appliedResp);
      }

      // Render a "raw" preview — DOCX as-is, placeholders visible, no
      // sample data. This shows HR what the template looks like and where
      // the {{ variables }} sit before any candidate is onboarded.
      setStage("rendering");
      const previewRes = await fetch(
        `/api/onboarding/templates/${documentId}/preview?raw=true`,
        { method: "POST" },
      );
      const previewBody = await readJson(previewRes);
      if (cancelled) return;
      if (!previewRes.ok) {
        // Fall through to "done" without preview — the analysis + mapping
        // already succeeded, the preview is a nice-to-have.
        setStage("done");
        return;
      }
      setPreviewUrl((previewBody as PreviewResponse).preview_url);
      setStage("done");
    })();

    return () => {
      cancelled = true;
    };
  }, [open, documentId]);

  const kindLabel = KIND_LABEL[templateKind] || templateKind;
  const mappings = analysis?.mappings ?? [];
  const placeholderCount = analysis
    ? (analysis.text_preview.match(/\{\{\s*[a-zA-Z_][a-zA-Z0-9_.]*\s*\}\}/g) || [])
        .length
    : 0;
  const stageLabel: Record<Stage, string> = {
    analyzing: "Reading template and finding blanks…",
    applying: `Applying ${mappings.length} mapping${mappings.length === 1 ? "" : "s"}…`,
    rendering: "Rendering preview…",
    done: "Template ready",
    replacing: "Uploading edited .docx and re-rendering…",
    saving: "Saving template…",
    saved: "Template saved",
    error: "Something went wrong",
  };

  const inProgress =
    stage === "analyzing" || stage === "applying" || stage === "rendering";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-500" />
            {stage === "saved"
              ? "Template saved"
              : stage === "done"
                ? "Review and save"
                : "Checking template"}
          </DialogTitle>
          <DialogDescription>
            {documentName ? (
              <>
                <span className="font-medium text-foreground">{documentName}</span>
                {" · "}
              </>
            ) : null}
            {kindLabel}
            {stage === "saved" ? (
              <> · this template is now live. The Onboarding agent will use it for every new candidate.</>
            ) : stage === "done" ? (
              <> · check the preview. Download the .docx if you need to edit, then re-upload. When the template looks right, click Save.</>
            ) : (
              <> · we&apos;ll find <code>{`{{ variables }}`}</code>, convert any blank spots into placeholders, and show the template as-is.</>
            )}
          </DialogDescription>
        </DialogHeader>

        {/* Progress strip */}
        {inProgress ? (
          <div className="space-y-3 py-2">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {stageLabel[stage]}
            </div>
            <ol className="space-y-1.5 text-xs">
              {(["analyzing", "applying", "rendering"] as const).map((s, i) => {
                const order = ["analyzing", "applying", "rendering"];
                const currentIdx = order.indexOf(stage);
                const thisIdx = order.indexOf(s);
                const done = thisIdx < currentIdx;
                const active = thisIdx === currentIdx;
                return (
                  <li
                    key={s}
                    className={
                      "flex items-center gap-2 " +
                      (active
                        ? "text-foreground"
                        : done
                          ? "text-emerald-600 dark:text-emerald-400"
                          : "text-muted-foreground")
                    }
                  >
                    <span
                      className={
                        "flex h-4 w-4 items-center justify-center rounded-full text-[10px] " +
                        (done
                          ? "bg-emerald-500 text-white"
                          : active
                            ? "bg-foreground text-background"
                            : "bg-muted")
                      }
                    >
                      {done ? "✓" : i + 1}
                    </span>
                    <span>
                      {s === "analyzing" && "Find {{ }} placeholders and blank spots"}
                      {s === "applying" && "Convert blanks into placeholders"}
                      {s === "rendering" && "Render template (placeholders visible, no sample data)"}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}

        {/* Error */}
        {stage === "error" && error ? (
          <div className="rounded-md border border-red-300/60 bg-red-50 p-3 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <p>{error}</p>
            </div>
          </div>
        ) : null}

        {/* AI warning (analyzer failed but we kept going) */}
        {stage === "done" && analysis?.warning ? (
          <div className="rounded-md border border-amber-300/60 bg-amber-50 p-2.5 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {analysis.warning}
          </div>
        ) : null}

        {/* Done — summary only, no sample-data preview */}
        {stage === "done" ? (
          <div className="space-y-3">
            <div className="rounded-md border border-emerald-300/60 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="flex-1">
                  <p className="font-medium">
                    {appliedCount > 0
                      ? `Converted ${appliedCount} blank${appliedCount === 1 ? "" : "s"} into ${appliedCount === 1 ? "a placeholder" : "placeholders"}.`
                      : analysis?.has_placeholders
                        ? `Template already has ${placeholderCount} placeholder${placeholderCount === 1 ? "" : "s"} — no blanks to convert.`
                        : "No placeholders or blanks detected — template will be used as-is."}
                  </p>
                  <p className="mt-0.5 text-xs opacity-80">
                    Placeholders will be filled with real candidate data when
                    you start each candidate&apos;s onboarding.
                  </p>
                </div>
              </div>
            </div>

            {mappings.length > 0 ? (
              <div className="rounded-md border border-border bg-muted/20 p-2">
                <button
                  type="button"
                  onClick={() => setShowMappings((v) => !v)}
                  className="mb-1 text-xs font-medium text-foreground underline hover:no-underline"
                >
                  {showMappings ? "Hide" : "Show"} {mappings.length} mapping
                  {mappings.length === 1 ? "" : "s"}
                </button>
                {showMappings ? (
                  <ul className="max-h-32 space-y-1 overflow-y-auto text-[11px]">
                    {mappings.map((m, i) => (
                      <li
                        key={`${m.blank_text}-${i}`}
                        className="flex items-center justify-between gap-2"
                      >
                        <code className="truncate rounded bg-background px-1.5 py-0.5 font-mono text-foreground">
                          {m.blank_text}
                        </code>
                        <span className="text-muted-foreground">→</span>
                        <code className="truncate rounded bg-background px-1.5 py-0.5 font-mono text-foreground">
                          {`{{ ${m.variable} }}`}
                        </code>
                        <span
                          className={
                            "rounded-full px-1.5 py-0.5 text-[10px] font-medium " +
                            CONFIDENCE_STYLE[m.confidence]
                          }
                        >
                          {m.confidence}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {previewUrl ? (
              <div className="overflow-hidden rounded-md border border-border bg-muted/20">
                <iframe
                  src={previewUrl}
                  title="Template preview (placeholders visible)"
                  className="h-[55vh] w-full bg-white"
                />
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Preview unavailable — the analysis and mapping were saved,
                but rendering the PDF failed. Try the &quot;Preview&quot; button
                on the templates page.
              </p>
            )}
          </div>
        ) : null}

        {/* Action-level errors (replace/save) — kept distinct from initial
            analyze/apply errors which use the larger banner above. */}
        {actionError ? (
          <div className="rounded-md border border-amber-300/60 bg-amber-50 p-2.5 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {actionError}
          </div>
        ) : null}

        <DialogFooter className="gap-2">
          <input
            ref={replaceFileRef}
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void replaceDocx(f);
              if (replaceFileRef.current) replaceFileRef.current.value = "";
            }}
          />
          {stage === "done" || stage === "replacing" || stage === "saving" ? (
            <>
              {previewUrl ? (
                <a
                  href={previewUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mr-auto inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
                >
                  Open in new tab <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
              <Button
                variant="outline"
                size="sm"
                onClick={downloadDocx}
                disabled={stage !== "done"}
              >
                <Download className="mr-1.5 h-3.5 w-3.5" />
                Download .docx
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => replaceFileRef.current?.click()}
                disabled={stage !== "done"}
              >
                {stage === "replacing" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Upload className="mr-1.5 h-3.5 w-3.5" />
                )}
                Replace with edited .docx
              </Button>
              <Button
                size="sm"
                onClick={saveTemplate}
                disabled={stage !== "done"}
              >
                {stage === "saving" ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : null}
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
