"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";
import type { MessageSource } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CitationsProps {
  sources: MessageSource[];
}

/**
 * Source attribution for an assistant message. Each chip expands inline to
 * show its excerpt; clicking "Open document" mints a fresh signed URL on
 * demand so we never embed long-lived links in the markup.
 */
export function Citations({ sources }: CitationsProps) {
  if (sources.length === 0) return null;

  // Group by document so two chunks from the same doc don't shout twice.
  const grouped = groupByDocument(sources);

  return (
    <div className="mt-4 border-t border-border/70 pt-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <span>Based on {grouped.length} {grouped.length === 1 ? "source" : "sources"}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {grouped.map((g) => (
          <CitationCard key={g.key} group={g} />
        ))}
      </div>
    </div>
  );
}

interface CitationGroup {
  key: string;
  document_name: string;
  document_id: string | null;
  pages: (number | null)[];
  excerpts: { page_number: number | null; excerpt: string }[];
}

function groupByDocument(sources: MessageSource[]): CitationGroup[] {
  const map = new Map<string, CitationGroup>();
  for (const s of sources) {
    const docId = s.document_id ?? null;
    const key = docId ?? s.document_name;
    if (!map.has(key)) {
      map.set(key, {
        key,
        document_name: s.document_name,
        document_id: docId,
        pages: [],
        excerpts: [],
      });
    }
    const g = map.get(key)!;
    if (s.page_number != null && !g.pages.includes(s.page_number)) {
      g.pages.push(s.page_number);
    }
    g.excerpts.push({ page_number: s.page_number, excerpt: s.excerpt });
  }
  for (const g of map.values()) {
    g.pages.sort((a, b) => (a ?? 0) - (b ?? 0));
  }
  return Array.from(map.values());
}

function CitationCard({ group }: { group: CitationGroup }) {
  const [open, setOpen] = useState(false);
  const [opening, setOpening] = useState(false);

  const pageLabel =
    group.pages.length === 0
      ? null
      : group.pages.length === 1
        ? `Page ${group.pages[0]}`
        : `${group.pages.length} pages`;

  const openDocument = async (preferredPage?: number | null) => {
    if (!group.document_id) {
      toast.error("Document link unavailable.");
      return;
    }
    setOpening(true);
    try {
      const res = await fetch(`/api/documents/${group.document_id}/signed-url`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.error ?? `Failed (${res.status})`);
      }
      const { url } = (await res.json()) as { url: string };
      // PDF page anchor — only meaningful when the browser's built-in PDF
      // viewer is rendering, but harmless on other formats. We detect PDF by
      // the original filename in either the document name or the signed URL.
      const page =
        preferredPage ?? (group.pages.length > 0 ? group.pages[0] : null);
      const isPdf =
        /\.pdf(\?|$)/i.test(url) || /\.pdf$/i.test(group.document_name);
      const target = page != null && isPdf ? `${url}#page=${page}` : url;
      window.open(target, "_blank", "noopener,noreferrer");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Could not open document.",
      );
    } finally {
      setOpening(false);
    }
  };

  return (
    <div
      className={cn(
        "min-w-[180px] max-w-full overflow-hidden rounded-lg border border-border bg-background text-left transition-colors",
        open && "shadow-sm",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 hover:bg-muted/50"
      >
        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-foreground" title={group.document_name}>
            {group.document_name}
          </p>
          {pageLabel && (
            <p className="truncate text-[11px] text-muted-foreground">{pageLabel}</p>
          )}
        </div>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="border-t border-border/70 bg-muted/30 px-3 py-2">
          <div className="space-y-2">
            {group.excerpts.slice(0, 3).map((e, i) => (
              <p
                key={i}
                className="text-[11.5px] leading-5 text-muted-foreground"
              >
                {e.page_number != null &&
                  (group.document_id ? (
                    <button
                      type="button"
                      onClick={() => openDocument(e.page_number)}
                      disabled={opening}
                      className="mr-1 rounded bg-background px-1 py-px text-[10px] font-medium text-foreground transition-colors hover:bg-primary/10 hover:text-primary disabled:opacity-50"
                      aria-label={`Open document at page ${e.page_number}`}
                    >
                      p.{e.page_number}
                    </button>
                  ) : (
                    <span className="mr-1 rounded bg-background px-1 py-px text-[10px] font-medium text-foreground">
                      p.{e.page_number}
                    </span>
                  ))}
                <span className="line-clamp-3">{e.excerpt}</span>
              </p>
            ))}
          </div>

          {group.document_id && (
            <button
              type="button"
              onClick={() => openDocument()}
              disabled={opening}
              className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline disabled:opacity-50"
            >
              {opening ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <ExternalLink className="h-3 w-3" />
              )}
              Open document
            </button>
          )}
        </div>
      )}
    </div>
  );
}
