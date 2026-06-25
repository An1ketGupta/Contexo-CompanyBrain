"use client";

import { useCallback, useState } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import { marked } from "marked";
import removeMarkdown from "remove-markdown";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

type CopyFormat = "markdown" | "plain" | "html";

interface CopyButtonProps {
  text: string;
  className?: string;
  label?: string;
  /** Persisted assistant-message id. When supplied, every successful copy
   *  pings POST /chat/messages/{id}/copied as a non-blocking quality signal. */
  messageId?: string | null;
}

const FORMAT_LABEL: Record<CopyFormat, string> = {
  markdown: "Markdown",
  plain: "Plain text",
  html: "Rich text",
};

function toPlain(md: string): string {
  return removeMarkdown(md, { useImgAltText: true }).replace(/\n{3,}/g, "\n\n").trim();
}

function toHtml(md: string): string {
  // marked is synchronous in default mode; cast to string to satisfy TS.
  const html = marked.parse(md, { async: false, breaks: true, gfm: true }) as string;
  return html.trim();
}

async function writeFormat(text: string, format: CopyFormat): Promise<boolean> {
  try {
    if (format === "html" && typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
      const html = toHtml(text);
      const plain = toPlain(text);
      const item = new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      });
      await navigator.clipboard.write([item]);
      return true;
    }
    const out = format === "plain" ? toPlain(text) : format === "html" ? toHtml(text) : text;
    await navigator.clipboard.writeText(out);
    return true;
  } catch {
    return false;
  }
}

export function CopyButton({ text, className, label = "Copy", messageId }: CopyButtonProps) {
  const [copied, setCopied] = useState<CopyFormat | null>(null);
  const [open, setOpen] = useState(false);

  const flash = useCallback((format: CopyFormat) => {
    setCopied(format);
    window.setTimeout(() => setCopied(null), 1500);
  }, []);

  const signal = useCallback(
    (format: CopyFormat) => {
      if (!messageId) return;
      void fetch(`/api/chat/messages/${messageId}/copied`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ format }),
      }).catch(() => {});
    },
    [messageId],
  );

  const doCopy = useCallback(
    async (format: CopyFormat) => {
      const ok = await writeFormat(text, format);
      if (ok) {
        flash(format);
        signal(format);
      }
    },
    [text, flash, signal],
  );

  const baseClasses = cn(
    "tap inline-flex items-center rounded-md border border-transparent text-xs font-medium text-muted-foreground transition-colors",
    "hover:border-border hover:bg-muted hover:text-foreground",
  );

  return (
    <div className={cn("inline-flex items-stretch", className)}>
      <button
        type="button"
        onClick={() => void doCopy("markdown")}
        className={cn(baseClasses, "gap-1.5 rounded-r-none px-2 py-1")}
        aria-label={copied ? "Copied" : label}
      >
        {copied ? (
          <>
            <Check className="h-3.5 w-3.5" />
            Copied {FORMAT_LABEL[copied].toLowerCase()}
          </>
        ) : (
          <>
            <Copy className="h-3.5 w-3.5" />
            {label}
          </>
        )}
      </button>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              baseClasses,
              "rounded-l-none border-l border-l-border/60 px-1.5 py-1",
            )}
            aria-label="Copy as…"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[12rem]">
          <DropdownMenuLabel>Copy as</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void doCopy("markdown")}>
            <Copy className="h-3.5 w-3.5" />
            <span className="flex-1">Markdown</span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">.md</span>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void doCopy("plain")}>
            <Copy className="h-3.5 w-3.5" />
            <span className="flex-1">Plain text</span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">txt</span>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void doCopy("html")}>
            <Copy className="h-3.5 w-3.5" />
            <span className="flex-1">Rich text</span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">html</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
