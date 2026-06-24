"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Pin, PinOff, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

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
            <Textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value.slice(0, MAX_LEN))}
              placeholder="e.g. We're writing for the Q3 enterprise renewal cohort. Reference the June 30 launch."
              rows={6}
              className="resize-none text-sm"
              disabled={saving}
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                {remaining} chars left
              </span>
              <div className="flex items-center gap-2">
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
