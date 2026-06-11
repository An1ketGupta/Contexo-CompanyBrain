"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Send, Square } from "lucide-react";
import { TemplatePopover } from "./template-popover";
import { cn } from "@/lib/utils";

interface MessageInputProps {
  onSend: (text: string) => void;
  onStop?: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  disabledReason?: string;
  placeholder?: string;
  /** Hard cap mirrored from FastAPI chat_max_message_chars. */
  maxLength?: number;
}

const DEFAULT_PLACEHOLDER =
  "Ask anything or describe a task — write an email, draft a job description, summarize a policy…";

export function MessageInput({
  onSend,
  onStop,
  isStreaming,
  disabled,
  disabledReason,
  placeholder = DEFAULT_PLACEHOLDER,
  maxLength = 16_000,
}: MessageInputProps) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea with a sensible ceiling (~6 lines worth).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    const next = Math.min(el.scrollHeight, 200);
    el.style.height = `${next}px`;
  }, [value]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled || isStreaming) return;
    onSend(trimmed);
    setValue("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const showSend = !isStreaming;
  const overLimit = value.length > maxLength;
  const canSubmit = !disabled && !isStreaming && value.trim().length > 0 && !overLimit;

  return (
    <div className="border-t border-border bg-background/95 px-4 py-3 backdrop-blur md:px-6">
      <div className="mx-auto max-w-3xl">
        {disabled && disabledReason && (
          <div className="mb-2 rounded-md border border-amber-300/40 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            {disabledReason}
          </div>
        )}

        <div
          className={cn(
            "flex items-end gap-2 rounded-2xl border border-input bg-background px-3 py-2 shadow-sm transition-colors focus-within:border-primary focus-within:ring-1 focus-within:ring-primary/30",
            disabled && "opacity-60",
          )}
        >
          {/* Template picker — pre-fills the textarea with a saved prompt. The
              popover anchors above this button so it doesn't clip the input. */}
          <div className="pb-1">
            <TemplatePopover
              onSelect={(text) => {
                setValue((prev) => (prev ? `${prev.trimEnd()}\n${text}` : text));
                ref.current?.focus();
              }}
            />
          </div>
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={placeholder}
            disabled={disabled || isStreaming}
            maxLength={maxLength + 200}
            className="block max-h-[200px] w-full resize-none border-0 bg-transparent text-sm leading-6 text-foreground placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed"
          />

          {showSend ? (
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors",
                canSubmit
                  ? "bg-primary text-primary-foreground hover:bg-primary/90"
                  : "bg-muted text-muted-foreground",
              )}
              aria-label="Send"
            >
              <Send className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={onStop}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-foreground text-background transition-colors hover:bg-foreground/90"
              aria-label="Stop"
              title="Stop generating"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </button>
          )}
        </div>

        <div className="mt-1.5 flex items-center justify-between px-1 text-[11px] text-muted-foreground">
          <span>Enter to send · Shift+Enter for new line</span>
          {value.length > maxLength * 0.8 && (
            <span className={cn(overLimit && "text-destructive")}>
              {value.length.toLocaleString()} / {maxLength.toLocaleString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
