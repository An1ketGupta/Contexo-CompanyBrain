"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDown, Clock, ExternalLink, FileText, Loader2 } from "lucide-react";
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
  const overdue = pickOverdueDocs(grouped);

  return (
    <div className="mt-4 border-t border-border/70 pt-3">
      {overdue.length > 0 && <OverdueBanner overdue={overdue} />}
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

interface OverdueDoc {
  document_id: string;
  document_name: string;
}

function pickOverdueDocs(groups: CitationGroup[]): OverdueDoc[] {
  const now = Date.now();
  const out: OverdueDoc[] = [];
  for (const g of groups) {
    if (!g.document_id || !g.review_due_at) continue;
    const due = Date.parse(g.review_due_at);
    if (Number.isFinite(due) && due < now) {
      out.push({ document_id: g.document_id, document_name: g.document_name });
    }
  }
  return out;
}

function OverdueBanner({ overdue }: { overdue: OverdueDoc[] }) {
  const first = overdue[0];
  const extra = overdue.length - 1;
  return (
    <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-400/30 dark:bg-amber-500/10 dark:text-amber-200">
      <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        {overdue.length === 1 ? (
          <>
            This answer is based on a document that may be outdated:{" "}
            <span className="font-medium">{first.document_name}</span>.{" "}
            <Link
              href="/documents"
              className="font-medium underline-offset-2 hover:underline"
            >
              Review →
            </Link>
          </>
        ) : (
          <>
            This answer cites {overdue.length} documents that may be outdated.{" "}
            <Link
              href="/documents"
              className="font-medium underline-offset-2 hover:underline"
            >
              Review {first.document_name}
              {extra > 0 ? ` (+${extra} more)` : ""} →
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

interface CitationGroup {
  key: string;
  document_name: string;
  document_id: string | null;
  version_number: number | null;
  pages: (number | null)[];
  excerpts: { page_number: number | null; excerpt: string }[];
  review_due_at: string | null;
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
        version_number: s.version_number ?? null,
        pages: [],
        excerpts: [],
        review_due_at: s.review_due_at ?? null,
      });
    }
    const g = map.get(key)!;
    if (s.page_number != null && !g.pages.includes(s.page_number)) {
      g.pages.push(s.page_number);
    }
    g.excerpts.push({ page_number: s.page_number, excerpt: s.excerpt });
    if (g.version_number == null && s.version_number != null) {
      g.version_number = s.version_number;
    }
    if (g.review_due_at == null && s.review_due_at) {
      g.review_due_at = s.review_due_at;
    }
  }
  for (const g of map.values()) {
    g.pages.sort((a, b) => (a ?? 0) - (b ?? 0));
  }
  return Array.from(map.values());
}

function groupExcerptsByPage(
  excerpts: { page_number: number | null; excerpt: string }[],
): { page_number: number | null; excerpts: string[] }[] {
  const map = new Map<string, { page_number: number | null; excerpts: string[] }>();
  const order: string[] = [];
  for (const e of excerpts) {
    const key = e.page_number == null ? "__none__" : String(e.page_number);
    if (!map.has(key)) {
      map.set(key, { page_number: e.page_number, excerpts: [] });
      order.push(key);
    }
    map.get(key)!.excerpts.push(e.excerpt);
  }
  return order.map((k) => map.get(k)!);
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
  const versionLabel =
    group.version_number != null ? `v${group.version_number}` : null;

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
          <div className="flex min-w-0 items-center gap-1.5">
            <p className="truncate text-xs font-medium text-foreground" title={group.document_name}>
              {group.document_name}
            </p>
            {versionLabel && (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                {versionLabel}
              </span>
            )}
          </div>
          {(pageLabel || versionLabel) && (
            <p className="truncate text-[11px] text-muted-foreground">
              {[pageLabel, versionLabel].filter(Boolean).join(" · ")}
            </p>
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
          <div className="space-y-3">
            {groupExcerptsByPage(group.excerpts).slice(0, 3).map((pg, i) => (
              <div key={i} className="space-y-1">
                {pg.page_number != null && (
                  <div className="flex items-center">
                    {group.document_id ? (
                      <button
                        type="button"
                        onClick={() => openDocument(pg.page_number)}
                        disabled={opening}
                        className="rounded-full bg-background px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-border/70 transition-colors hover:bg-primary/10 hover:text-primary hover:ring-primary/40 disabled:opacity-50"
                        aria-label={`Open document at page ${pg.page_number}`}
                      >
                        Page {pg.page_number}
                      </button>
                    ) : (
                      <span className="rounded-full bg-background px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground ring-1 ring-border/70">
                        Page {pg.page_number}
                      </span>
                    )}
                  </div>
                )}
                <div className="space-y-1">
                  {pg.excerpts.map((excerpt, j) => (
                    <p
                      key={j}
                      className="line-clamp-3 text-[11.5px] leading-5 text-muted-foreground"
                    >
                      {excerpt}
                    </p>
                  ))}
                </div>
              </div>
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
