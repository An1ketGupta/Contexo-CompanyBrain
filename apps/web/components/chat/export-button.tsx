"use client";

import { useState } from "react";
import { Copy, Download, FileText, Loader2, Printer } from "lucide-react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { parseApiError, type ApiError } from "@/lib/errors";

interface ExportButtonProps {
  conversationId: string;
  title: string;
}

/**
 * V3 Day 4 #25 — three-way export menu on the conversation header.
 *
 * Markdown is a direct download; PDF opens a print-friendly route in a new
 * tab so the user can use the OS print dialog (works on every browser, no
 * native PDF lib needed); Copy-all puts the markdown on the clipboard.
 */
export function ExportButton({ conversationId, title }: ExportButtonProps) {
  const [busy, setBusy] = useState<null | "markdown" | "copy">(null);

  const exportMarkdown = async () => {
    setBusy("markdown");
    try {
      const res = await fetch(
        `/api/chat/conversations/${encodeURIComponent(conversationId)}/export?format=markdown`,
      );
      if (!res.ok) throw await parseApiError(res);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${slugify(title) || "conversation"}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      const apiErr = err as ApiError;
      toast.error(apiErr.message ?? "Couldn't export conversation.");
    } finally {
      setBusy(null);
    }
  };

  const openPrint = () => {
    window.open(
      `/print/conversation/${encodeURIComponent(conversationId)}`,
      "_blank",
      "noopener,noreferrer",
    );
  };

  const copyAll = async () => {
    setBusy("copy");
    try {
      const res = await fetch(
        `/api/chat/conversations/${encodeURIComponent(conversationId)}/export?format=markdown`,
      );
      if (!res.ok) throw await parseApiError(res);
      const text = await res.text();
      await navigator.clipboard.writeText(text);
      toast.success("Copied conversation to clipboard");
    } catch (err) {
      const apiErr = err as ApiError;
      toast.error(apiErr.message ?? "Couldn't copy conversation.");
    } finally {
      setBusy(null);
    }
  };

  const isBusy = busy !== null;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={isBusy}
          className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-background px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50"
          aria-label="Export conversation"
          title="Export conversation"
        >
          {isBusy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          <span className="hidden sm:inline">Export</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem onClick={exportMarkdown} disabled={isBusy}>
          <FileText className="h-3.5 w-3.5" />
          Download as Markdown
        </DropdownMenuItem>
        <DropdownMenuItem onClick={openPrint}>
          <Printer className="h-3.5 w-3.5" />
          Save as PDF
        </DropdownMenuItem>
        <DropdownMenuItem onClick={copyAll} disabled={isBusy}>
          <Copy className="h-3.5 w-3.5" />
          Copy all text
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function slugify(t: string): string {
  return t
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}
