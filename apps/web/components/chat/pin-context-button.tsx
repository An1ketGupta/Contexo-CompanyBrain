"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BookmarkPlus, Pin, PinOff, Sparkles, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useTemplates } from "@/hooks/use-templates";
import type { PromptTemplate } from "@/lib/types";

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
        className="gap-1.5 text-xs"
        aria-label={isPinned ? "Edit pinned context" : "Pin context for this conversation"}
        onClick={() => setOpen((v) => !v)}
      >
        {isPinned ? <Pin className="size-3.5" /> : <PinOff className="size-3.5" />}
        {isPinned ? "Pinned" : "Pin context"}
      </Button>
      {open && (
        <div
          ref={popoverRef}
          className="absolute right-0 z-50 mt-2 w-96 rounded-md border border-border bg-popover p-3 text-popover-foreground shadow-md"
          role="dialog"
        >
          <div className="space-y-2">
            <div>
              <p className="text-sm font-medium">Pin context for this conversation</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                The LLM will keep this in mind across every turn. Use it to set
                audience, project, or constraints once instead of repeating yourself.
              </p>
            </div>
            {contextTemplates.length > 0 && (
              <div className="rounded-md border border-border bg-muted/30 px-2 py-1.5 text-xs">
                <div className="mb-1 flex items-center gap-1 font-medium text-muted-foreground">
                  <Sparkles className="size-3" /> Apply a saved context
                </div>
                <div className="flex flex-wrap gap-1">
                  {contextTemplates.slice(0, 6).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => applyTemplate(t)}
                      disabled={saving}
                      className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px] hover:bg-primary/5 hover:border-primary/40 disabled:opacity-50"
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
              className="resize-none text-sm"
              disabled={saving}
            />
            {savingTemplateOpen && (
              <div className="space-y-1.5 rounded-md border border-dashed border-border px-2 py-2">
                <div className="text-[11px] font-medium text-muted-foreground">
                  Save current context as a reusable template
                </div>
                <input
                  value={templateTitle}
                  onChange={(e) => setTemplateTitle(e.target.value.slice(0, 120))}
                  placeholder="Title (e.g. Q3 Enterprise renewals)"
                  className="w-full rounded-md border border-input bg-background px-2 py-1 text-xs outline-none focus:ring-1 focus:ring-ring"
                  disabled={savingTemplate}
                />
                <div className="flex justify-end gap-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setSavingTemplateOpen(false);
                      setTemplateTitle("");
                    }}
                    className="text-[11px] text-muted-foreground hover:text-foreground"
                    disabled={savingTemplate}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={saveAsTemplate}
                    disabled={savingTemplate || !templateTitle.trim() || !draft.trim()}
                    className="rounded-md bg-primary px-2 py-0.5 text-[11px] font-medium text-primary-foreground disabled:opacity-50"
                  >
                    {savingTemplate ? "Saving…" : "Save template"}
                  </button>
                </div>
              </div>
            )}
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {remaining} chars left
              </span>
              <div className="flex items-center gap-2">
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
                    className="text-xs"
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
        </div>
      )}
    </div>
  );
}
