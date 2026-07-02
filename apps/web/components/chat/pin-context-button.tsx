"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BookmarkPlus, Pin, PinOff, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useTemplates } from "@/hooks/use-templates";
import type { PromptTemplate } from "@/lib/types";
import { cn } from "@/lib/utils";

// Agent2 Day 2 #39 — per-conversation "pinned context" the user wants the
// LLM to keep in mind across every turn. Stored on conversations.pinned_context,
// capped at 2000 chars. Injected into the system prompt by task_chain.py.
//
// Hand-rolled popover (no Radix dep) — matches the pattern in scope-popover.tsx.

const MAX_LEN = 2000;

interface PinContextButtonProps {
  conversationId: string;
  initialValue: string | null;
  onSaved?: (next: string | null) => void;
}

export function PinContextButton({
  conversationId,
  initialValue,
  onSaved,
}: PinContextButtonProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(initialValue ?? "");
  const [saving, setSaving] = useState(false);
  const [savedValue, setSavedValue] = useState<string | null>(initialValue);
  // Production Roadmap 1.7 — context template surface inside the popover.
  // Two affordances: "Apply a saved context" (replaces draft) + "Save as
  // template" (persists current draft for reuse on future conversations).
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [templateTitle, setTemplateTitle] = useState("");
  const [savingTemplateOpen, setSavingTemplateOpen] = useState(false);
  // Load only context templates so the picker stays focused on preambles.
  const { templates: contextTemplates, createTemplate } = useTemplates(
    "All",
    "",
    "context",
  );
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    setSavedValue(initialValue);
    setDraft(initialValue ?? "");
  }, [initialValue]);

  // Outside-click + escape to close.
  useEffect(() => {
    if (!open) return;
    const onClick = (ev: MouseEvent) => {
      if (popoverRef.current?.contains(ev.target as Node)) return;
      if (triggerRef.current?.contains(ev.target as Node)) return;
      setOpen(false);
    };
    const onEsc = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const isPinned = !!savedValue && savedValue.trim().length > 0;
  const dirty = (draft.trim() || null) !== (savedValue || null);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const trimmed = draft.trim();
      const body = trimmed
        ? { pinned_context: trimmed }
        : { clear_pinned_context: true };
      const res = await fetch(
        `/api/conversations/${encodeURIComponent(conversationId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      if (!res.ok) {
        const msg = await res.text();
        throw new Error(msg || `Failed to save (${res.status})`);
      }
      const next = trimmed || null;
      setSavedValue(next);
      onSaved?.(next);
      toast.success(next ? "Context pinned for this conversation." : "Context unpinned.");
      setOpen(false);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [conversationId, draft, onSaved]);

  const clearAll = useCallback(async () => {
    setDraft("");
    setSaving(true);
    try {
      const res = await fetch(
        `/api/conversations/${encodeURIComponent(conversationId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clear_pinned_context: true }),
        },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      setSavedValue(null);
      onSaved?.(null);
      toast.success("Context unpinned.");
      setOpen(false);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  }, [conversationId, onSaved]);

  const applyTemplate = useCallback((t: PromptTemplate) => {
    const next = (t.pinned_context ?? "").slice(0, MAX_LEN);
    setDraft(next);
    // Record popularity in the background — same fire-and-forget as the
    // prompt-template picker. The draft is what matters now.
    fetch(`/api/templates/${t.id}/use`, { method: "POST" }).catch(() => {});
  }, []);

  const saveAsTemplate = useCallback(async () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      toast.error("Pinned context is empty.");
      return;
    }
    const title = templateTitle.trim();
    if (!title) {
      toast.error("Give the template a title so you can find it later.");
      return;
    }
    setSavingTemplate(true);
    try {
      await createTemplate({
        title,
        description: null,
        // Required field on the API — context templates store payload in
        // pinned_context instead. We send the first 200 chars of the
        // preamble as the template_text to satisfy validation; clients
        // never display it for is_context_template rows.
        template_text: trimmed.slice(0, 200),
        category: "Other",
        is_shared: false,
        is_context_template: true,
        pinned_context: trimmed,
      });
      toast.success("Context template saved.");
      setTemplateTitle("");
      setSavingTemplateOpen(false);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSavingTemplate(false);
    }
  }, [createTemplate, draft, templateTitle]);

  const remaining = MAX_LEN - draft.length;

  return (
    <div className="relative">
      <Button
        ref={triggerRef}
        variant={isPinned ? "secondary" : "ghost"}
        size="sm"
        className={cn(
          "gap-1.5 text-xs",
          isPinned && "bg-brand-tint text-brand hover:bg-brand-tint/80",
        )}
        aria-label={isPinned ? "Edit pinned context" : "Pin context for this conversation"}
        onClick={() => setOpen((v) => !v)}
      >
        {isPinned ? <Pin className="size-3.5" /> : <PinOff className="size-3.5" />}
        {isPinned ? "Pinned" : "Pin context"}
      </Button>
      {open && (
        <div
          ref={popoverRef}
          className="absolute right-0 z-50 mt-2 w-[26rem] overflow-hidden rounded-2xl border border-border bg-background text-foreground shadow-[0_16px_48px_-16px_rgba(16,24,40,0.28)]"
          role="dialog"
        >
          {/* Header — tinted pin badge + intent, the Actual card lead-in. */}
          <div className="flex items-start gap-3 px-4 pb-3 pt-4">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand-tint text-brand">
              <Pin className="size-4" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-bold tracking-tight text-foreground py-1">
                Pin context for this conversation
              </p>
            </div>
          </div>

          <div className="space-y-3 px-4">
            {contextTemplates.length > 0 && (
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  <Sparkles className="size-3 text-brand" /> Apply a saved context
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {contextTemplates.slice(0, 6).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => applyTemplate(t)}
                      disabled={saving}
                      className="rounded-full border border-border bg-muted px-2.5 py-1 text-[11px] font-semibold text-body transition-colors hover:border-brand/40 hover:bg-brand-tint hover:text-brand disabled:opacity-50"
                      title={t.pinned_context ?? ""}
                    >
                      {t.title}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value.slice(0, MAX_LEN))}
              placeholder="e.g. We're writing for the Q3 enterprise renewal cohort. Reference the June 30 launch."
              rows={6}
              className="min-h-28 resize-none rounded-xl text-sm leading-relaxed"
              disabled={saving}
            />
            {savingTemplateOpen && (
              <div className="space-y-2 rounded-xl border border-dashed border-border bg-muted/40 px-3 py-2.5">
                <div className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  Save as a reusable template
                </div>
                <input
                  value={templateTitle}
                  onChange={(e) => setTemplateTitle(e.target.value.slice(0, 120))}
                  placeholder="Title (e.g. Q3 Enterprise renewals)"
                  className="w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs outline-none focus:ring-2 focus:ring-ring"
                  disabled={savingTemplate}
                />
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSavingTemplateOpen(false);
                      setTemplateTitle("");
                    }}
                    className="text-[11px] font-semibold text-muted-foreground transition-colors hover:text-foreground"
                    disabled={savingTemplate}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={saveAsTemplate}
                    disabled={savingTemplate || !templateTitle.trim() || !draft.trim()}
                    className="rounded-full bg-primary px-3 py-1 text-[11px] font-bold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                  >
                    {savingTemplate ? "Saving…" : "Save template"}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Footer — mono char meter on the left, actions on the right. */}
          <div className="mt-3 flex items-center justify-between gap-2 border-t border-border bg-muted/40 px-4 py-3">
            <span
              className={cn(
                "font-mono text-[11px] font-bold uppercase tracking-wider tabular-nums",
                remaining <= 100 ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {remaining} chars left
            </span>
            <div className="flex items-center gap-1">
              {draft.trim() && !savingTemplateOpen && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSavingTemplateOpen(true)}
                  disabled={saving}
                  className="text-xs"
                  title="Save this preamble as a reusable context template"
                >
                  <BookmarkPlus className="mr-1 size-3" /> Save as template
                </Button>
              )}
              {isPinned && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={clearAll}
                  disabled={saving}
                  className="text-xs text-destructive hover:bg-destructive-soft hover:text-destructive"
                >
                  <X className="mr-1 size-3" /> Unpin
                </Button>
              )}
              <Button
                size="sm"
                onClick={save}
                disabled={saving || !dirty}
                className="text-xs"
              >
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
