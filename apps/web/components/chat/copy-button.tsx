"use client";

import { useCallback, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

interface CopyButtonProps {
  text: string;
  className?: string;
  label?: string;
  /** Persisted assistant-message id. When supplied, every successful copy
   *  pings POST /chat/messages/{id}/copied as a non-blocking quality signal
   *  (V5 #59). Optional so other call sites (sharing, public pages) can
   *  reuse this button without firing the signal. */
  messageId?: string | null;
}

export function CopyButton({ text, className, label = "Copy", messageId }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
      // V5 #59 — fire-and-forget. We deliberately don't await so a slow
      // backend doesn't delay the "Copied" affordance; failures are silent
      // because this is a side-channel signal, not user-facing state.
      if (messageId) {
        void fetch(`/api/chat/messages/${messageId}/copied`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        }).catch(() => {});
      }
    } catch {
      // Clipboard can fail in insecure contexts — silently no-op rather than
      // showing a scary toast. User can always select + copy manually.
    }
  }, [text, messageId]);

  return (
    <button
      type="button"
      onClick={onCopy}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-border hover:bg-muted hover:text-foreground",
        className,
      )}
      aria-label={copied ? "Copied" : label}
    >
      {copied ? (
        <>
          <Check className="h-3.5 w-3.5" />
          Copied
        </>
      ) : (
        <>
          <Copy className="h-3.5 w-3.5" />
          {label}
        </>
      )}
    </button>
  );
}
