"use client";

import { useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Loader2, MapPin, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { DocTemplateVariable } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Point a field at a place in the document.
 *
 * HR picks a paragraph, then selects the exact text to replace. The offsets
 * come from the browser's own selection against the same string the renderer
 * splices into — `paragraph_run_text` on the backend — so what they highlight
 * is precisely what gets overwritten. No coordinate translation, nothing to
 * drift.
 */

interface Paragraph {
  index: number;
  kind: string;
  text: string;
}

const fetcher = async (url: string): Promise<{ paragraphs: Paragraph[] }> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return { paragraphs: [] };
  return res.json();
};

export function PlaceFieldDialog({
  open,
  onOpenChange,
  versionId,
  variables,
  onPlace,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  versionId: string | null;
  variables: DocTemplateVariable[];
  onPlace: (input: {
    variable_id: string;
    paragraph_index: number;
    start_offset: number;
    end_offset: number;
    action: "replace_span" | "insert_after_label";
  }) => Promise<unknown>;
}) {
  const { data, isLoading } = useSWR(
    open && versionId
      ? `/api/document-templates/versions/${versionId}/text`
      : null,
    fetcher,
  );

  const inputRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Paragraph | null>(null);
  const [range, setRange] = useState<{ start: number; end: number } | null>(null);
  const [variableId, setVariableId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const paragraphs = data?.paragraphs ?? [];
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return paragraphs.slice(0, 60);
    return paragraphs
      .filter((p) => p.text.toLowerCase().includes(needle))
      .slice(0, 60);
  }, [paragraphs, search]);

  function reset() {
    setSearch("");
    setSelected(null);
    setRange(null);
    setVariableId("");
    setError(null);
  }

  function captureSelection() {
    const el = inputRef.current;
    if (!el) return;
    const start = el.selectionStart ?? 0;
    const end = el.selectionEnd ?? 0;
    setRange(start === end ? null : { start, end });
  }

  async function submit() {
    if (!selected || !variableId) return;
    setSubmitting(true);
    setError(null);
    try {
      // No highlighted text means "append at the end of this line" — the
      // `Employee Signature:` case, where there is nothing to replace.
      const insertion = range === null;
      await onPlace({
        variable_id: variableId,
        paragraph_index: selected.index,
        start_offset: insertion ? selected.text.length : range.start,
        end_offset: insertion ? selected.text.length : range.end,
        action: insertion ? "insert_after_label" : "replace_span",
      });
      onOpenChange(false);
      reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't place the field");
    } finally {
      setSubmitting(false);
    }
  }

  const preview = selected
    ? range
      ? selected.text.slice(range.start, range.end)
      : null
    : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Place a field in the document</DialogTitle>
          <DialogDescription>
            Find the line, highlight the text that should be replaced, and
            choose which field fills it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="place-search">Find a line</Label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                id="place-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="e.g. Joining Date"
                className="pl-9"
              />
            </div>
          </div>

          <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border p-1">
            {isLoading ? (
              <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Reading the document…
              </div>
            ) : filtered.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">
                No lines match that search.
              </p>
            ) : (
              filtered.map((p) => (
                <button
                  key={p.index}
                  type="button"
                  onClick={() => {
                    setSelected(p);
                    setRange(null);
                  }}
                  className={cn(
                    "block w-full truncate rounded-md px-2 py-1.5 text-left text-xs",
                    selected?.index === p.index
                      ? "bg-brand-tint font-semibold text-brand"
                      : "hover:bg-muted",
                  )}
                >
                  {p.text}
                </button>
              ))
            )}
          </div>

          {selected ? (
            <div className="space-y-2">
              <Label htmlFor="place-line">
                Highlight the text to replace
              </Label>
              <Input
                id="place-line"
                ref={inputRef}
                readOnly
                value={selected.text}
                onSelect={captureSelection}
                onKeyUp={captureSelection}
                onMouseUp={captureSelection}
                className="font-mono text-xs"
              />
              {preview ? (
                <p className="text-xs text-muted-foreground">
                  Will replace{" "}
                  <mark className="rounded bg-brand-tint px-1 font-semibold text-brand">
                    {preview}
                  </mark>
                </p>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Nothing highlighted — the value will be added at the end of
                  this line.
                </p>
              )}
            </div>
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="place-variable">Field</Label>
            <Select value={variableId} onValueChange={setVariableId}>
              <SelectTrigger id="place-variable">
                <SelectValue
                  placeholder={
                    variables.length === 0
                      ? "Confirm a field first"
                      : "Choose a field"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {variables.map((variable) => (
                  <SelectItem key={variable.id} value={variable.id}>
                    {variable.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {error ? (
            <div className="flex items-start gap-2 rounded-lg bg-destructive-soft px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={!selected || !variableId || submitting}
          >
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <MapPin className="mr-2 h-4 w-4" />
            )}
            Place field
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
