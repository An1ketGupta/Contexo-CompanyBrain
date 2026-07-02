"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useDocumentTags } from "@/hooks/use-documents";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // When set, dialog is in "bulk add to N docs" mode.
  bulkCount?: number;
  // Pre-existing tags shown as removable chips (single-doc edit mode).
  initial?: string[];
  title?: string;
  submitLabel?: string;
  busy?: boolean;
  onSubmit: (tags: string[]) => Promise<void> | void;
}

const TAG_PATTERN = /^[a-z0-9][a-z0-9\-_ .]{0,31}$/;

function normalize(raw: string): string | null {
  const cleaned = raw.trim().toLowerCase();
  if (!cleaned) return null;
  if (!TAG_PATTERN.test(cleaned)) return null;
  return cleaned;
}

export function TagDialog({
  open,
  onOpenChange,
  bulkCount,
  initial,
  title,
  submitLabel,
  busy = false,
  onSubmit,
}: Props) {
  const [pending, setPending] = useState<string[]>(initial ?? []);
  const [draft, setDraft] = useState("");
  const { tags: allTags } = useDocumentTags();

  // Reset state when the dialog opens.
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setPending(initial ?? []);
      setDraft("");
    }
    onOpenChange(next);
  };

  const add = (raw: string) => {
    const norm = normalize(raw);
    if (!norm) return;
    if (pending.includes(norm)) return;
    if (pending.length >= 20) return;
    setPending((prev) => [...prev, norm]);
    setDraft("");
  };

  const remove = (tag: string) => {
    setPending((prev) => prev.filter((t) => t !== tag));
  };

  const submit = async () => {
    if (pending.length === 0) return;
    await onSubmit(pending);
  };

  const suggestions = allTags
    .map((t) => t.tag)
    .filter((t) => !pending.includes(t))
    .slice(0, 12);

  const headline =
    title ??
    (bulkCount
      ? `Tag ${bulkCount} document${bulkCount === 1 ? "" : "s"}`
      : "Edit tags");
  const cta = submitLabel ?? (bulkCount ? "Apply tags" : "Save tags");
  const description = bulkCount
    ? "Tags will be added to the selected documents. Existing tags on each document are preserved."
    : "These tags fully replace whatever is currently on this document.";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{headline}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-input bg-background p-2 min-h-10">
            {pending.length === 0 && (
              <span className="text-xs text-muted-foreground">No tags yet.</span>
            )}
            {pending.map((tag) => (
              <span
                key={tag}
                className="inline-flex items-center gap-1 rounded-full bg-brand-tint px-2 py-0.5 text-xs font-semibold text-brand"
              >
                {tag}
                <button
                  type="button"
                  onClick={() => remove(tag)}
                  aria-label={`Remove ${tag}`}
                  className="-mr-0.5 rounded-full opacity-70 hover:opacity-100"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === ",") {
                  e.preventDefault();
                  add(draft);
                }
                if (e.key === "Backspace" && !draft && pending.length > 0) {
                  setPending((prev) => prev.slice(0, -1));
                }
              }}
              placeholder="Add a tag and press Enter…"
              maxLength={32}
              autoFocus
            />
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => add(draft)}
              disabled={!normalize(draft)}
            >
              <Plus className="h-3.5 w-3.5" />
              Add
            </Button>
          </div>

          {suggestions.length > 0 && (
            <div>
              <p className="mb-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                Existing tags
              </p>
              <div className="flex flex-wrap gap-1.5">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => add(s)}
                    className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] font-semibold text-muted-foreground transition-colors hover:border-brand/40 hover:bg-brand-tint hover:text-brand"
                  >
                    + {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <p className="text-[11px] text-muted-foreground">
            Lowercase letters, digits, and{" "}
            <code className="rounded bg-muted px-1">- _ . space</code> only. 32 chars max.
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || pending.length === 0}>
            {cta}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
