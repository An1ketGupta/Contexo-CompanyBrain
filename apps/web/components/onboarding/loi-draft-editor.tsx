"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, RotateCcw, Save, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Edit the LOIdraft as text, in the page.
 *
 * One textarea per paragraph of the rendered .docx. Saving posts back only the
 * lines that changed, and the backend splices them into the same file — so a
 * paragraph nobody touched keeps its formatting exactly. Editing a line does
 * flatten that line to one style, which is why the .docx round-trip stays
 * available for structural edits.
 *
 * `index` is the document's own paragraph numbering, not a position in this
 * list: blank paragraphs are omitted but the numbers are not renumbered.
 */

interface DraftParagraph {
  index: number;
  kind: string;
  text: string;
}

interface DraftText {
  revision: number;
  fingerprint: string;
  paragraphs: DraftParagraph[];
}

interface EditTextResult {
  status: string;
  revision: number;
  changed_count: number;
  fingerprint: string;
  preview_url: string | null;
}

/** Table cells, headers and footers read as fragments out of context — say
 *  where they came from so HR isn't editing a stray line blind. */
const KIND_LABEL: Record<string, string> = {
  table: "table",
  header: "header",
  footer: "footer",
};

async function readJson(res: Response): Promise<Record<string, unknown>> {
  return (await res.json().catch(() => ({}))) as Record<string, unknown>;
}

function errorText(body: Record<string, unknown>, fallback: string): string {
  if (typeof body.detail === "string") return body.detail;
  if (typeof body.message === "string") return body.message;
  return fallback;
}

/** Grows to fit its content — LOIlines run from one word to a full clause. */
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
      className="w-full resize-none overflow-hidden rounded-sm border-0 bg-transparent px-1 py-0.5 text-[15px] leading-7 text-foreground outline-none focus:bg-brand-tint disabled:opacity-60"
    />
  );
}

export function LOIDraftEditor({
  runId,
  onSaved,
  onClose,
}: {
  runId: string;
  /** Saved a new revision — the caller refreshes the run so the preview and
   *  the "edited (rev n)" badge pick up the new PDF. */
  onSaved: () => Promise<unknown> | void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<DraftText | null>(null);
  const [edited, setEdited] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A fingerprint mismatch can't be fixed by retrying the save — the draft
  // itself moved, so the only way forward is to reload it.
  const [stale, setStale] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStale(false);
    try {
      const res = await fetch(`/api/onboarding/runs/${runId}/loi/draft-text`);
      const body = await readJson(res);
      if (!res.ok) {
        setError(errorText(body, "Couldn't open the draft for editing."));
        return;
      }
      setDraft(body as unknown as DraftText);
      setEdited({});
    } catch {
      setError("Couldn't reach the server. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    load();
  }, [load]);

  const paragraphs = draft?.paragraphs ?? [];
  const textOf = (p: DraftParagraph) =>
    edited[p.index] !== undefined ? edited[p.index] : p.text;
  const changed = paragraphs.filter((p) => textOf(p) !== p.text);

  async function save() {
    if (!draft || changed.length === 0) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/onboarding/runs/${runId}/loi/edit-text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fingerprint: draft.fingerprint,
          edits: changed.map((p) => ({
            index: p.index,
            kind: p.kind,
            text: textOf(p),
          })),
        }),
      });
      const body = await readJson(res);
      if (!res.ok) {
        if (res.status === 409) setStale(true);
        setError(errorText(body, "Couldn't save your edits."));
        return;
      }

      // The saved text is the new baseline, so the panel closes clean rather
      // than warning about unsaved changes that are already stored.
      const result = body as unknown as EditTextResult;
      setDraft({
        revision: result.revision,
        fingerprint: result.fingerprint,
        paragraphs: paragraphs.map((p) => ({ ...p, text: textOf(p) })),
      });
      setEdited({});
      await onSaved();
      onClose();
    } catch {
      setError("Couldn't reach the server. Your edits are still on screen.");
    } finally {
      setSaving(false);
    }
  }

  function cancel() {
    if (
      changed.length > 0 &&
      !confirm(
        `Discard ${changed.length} unsaved change${
          changed.length === 1 ? "" : "s"
        } to the LOI?`,
      )
    ) {
      return;
    }
    onClose();
  }

  return (
    <div className="space-y-3">
      {loading ? (
        <div className="flex items-center gap-2 rounded-xl border border-border bg-background px-4 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Opening the draft…
        </div>
      ) : paragraphs.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-background px-4 py-8 text-center text-sm text-muted-foreground">
          No editable text found in this draft. Use{" "}
          <span className="font-medium text-foreground">Download .docx</span> to
          change it in Word instead.
        </div>
      ) : (
        <div className="max-h-[52vh] overflow-y-auto rounded-xl border border-border bg-background px-6 py-5 shadow-inner">
          <div className="mx-auto max-w-2xl space-y-1">
            {paragraphs.map((p) => {
              const badge = KIND_LABEL[p.kind];
              const isChanged = textOf(p) !== p.text;
              return (
                <div key={p.index} className="group relative">
                  {badge ? (
                    <span className="pointer-events-none absolute -left-6 top-1.5 hidden text-[9px] font-semibold uppercase tracking-wide text-muted-foreground group-focus-within:inline">
                      {badge}
                    </span>
                  ) : null}
                  <div
                    className={cn(
                      "rounded-sm",
                      isChanged && "bg-amber-tint/60 ring-1 ring-amber/30",
                    )}
                  >
                    <AutoTextarea
                      value={textOf(p)}
                      onChange={(v) =>
                        setEdited((prev) => ({ ...prev, [p.index]: v }))
                      }
                      disabled={saving}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {error ? (
        <div className="flex items-start gap-2 rounded-lg bg-destructive-soft px-3 py-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {stale ? (
          <Button size="sm" onClick={load} disabled={loading}>
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Reload the draft
          </Button>
        ) : (
          <Button
            size="sm"
            onClick={save}
            disabled={saving || loading || changed.length === 0}
          >
            {saving ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="mr-1.5 h-3.5 w-3.5" />
            )}
            Save and re-render
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={cancel} disabled={saving}>
          <X className="mr-1.5 h-3.5 w-3.5" />
          Cancel
        </Button>
        <p className="text-[11px] text-muted-foreground">
          {changed.length === 0
            ? "Edit any line above. Formatting is kept on every line you don't touch."
            : `${changed.length} line${
                changed.length === 1 ? "" : "s"
              } changed — saving re-renders the PDF preview.`}
        </p>
      </div>
    </div>
  );
}
