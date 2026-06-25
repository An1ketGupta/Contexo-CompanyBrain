"use client";

/**
 * Production Roadmap 1.6 — Inline Document Preview.
 *
 * Bottom/side sheet that renders a citation's source document without the
 * user leaving the chat. Two render modes, picked from the document name's
 * extension:
 *
 *  - PDF → signed URL in an <iframe>. The browser's built-in PDF viewer
 *    handles the rendering; we pass `#page=N` so Chrome/Edge jump to the
 *    cited page (Safari ignores it, which is fine — the doc still opens).
 *  - Anything else → fetch /documents/{id}/chunks and render the parsed
 *    text we already have in the DB. Highlights the cited chunk_index
 *    when supplied.
 *
 * The sheet is large (right-side, w-3/4 max) so PDFs render readably. We
 * deliberately don't render PDF chunks as text — the browser PDF viewer
 * is better at PDFs than text-from-chunks.
 *
 * Failure modes:
 *  - signed URL mint fails → fallback to text-chunk path if any
 *    (DOCX/HTML/MD), otherwise "Open in new tab" link only.
 *  - chunks fetch fails → "Open document" link as last resort.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

interface ChunkRow {
  chunk_index: number;
  page_number: number | null;
  section_heading: string | null;
  content: string;
}

interface ChunksResponse {
  document: { id: string; name: string; file_type: string | null };
  chunks: ChunkRow[];
  truncated: boolean;
}

interface DocumentPreviewSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string | null;
  documentName: string | null;
  preferredPage?: number | null;
}

function inferIsPdf(name: string | null): boolean {
  if (!name) return false;
  return /\.pdf$/i.test(name.trim());
}

export function DocumentPreviewSheet({
  open,
  onOpenChange,
  documentId,
  documentName,
  preferredPage,
}: DocumentPreviewSheetProps) {
  const isPdf = inferIsPdf(documentName);
  const [loading, setLoading] = useState(false);
  const [signedUrl, setSignedUrl] = useState<string | null>(null);
  const [chunks, setChunks] = useState<ChunksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Reset transient state whenever the sheet opens for a new document so a
  // stale iframe doesn't flash between previews.
  useEffect(() => {
    if (!open || !documentId) {
      setSignedUrl(null);
      setChunks(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    const load = async () => {
      try {
        if (isPdf) {
          const res = await fetch(
            `/api/documents/${encodeURIComponent(documentId)}/signed-url`,
          );
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail ?? body.error ?? `Failed (${res.status})`);
          }
          const data = (await res.json()) as { url: string };
          if (!cancelled) setSignedUrl(data.url);
        } else {
          const res = await fetch(
            `/api/documents/${encodeURIComponent(documentId)}/chunks`,
          );
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail ?? body.error ?? `Failed (${res.status})`);
          }
          const data = (await res.json()) as ChunksResponse;
          if (!cancelled) setChunks(data);
        }
      } catch (err) {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Failed to load preview.";
          setError(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [open, documentId, isPdf]);

  const openInNewTab = useCallback(async () => {
    if (!documentId) return;
    try {
      const res = await fetch(
        `/api/documents/${encodeURIComponent(documentId)}/signed-url`,
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const { url } = (await res.json()) as { url: string };
      const target =
        preferredPage != null && isPdf ? `${url}#page=${preferredPage}` : url;
      window.open(target, "_blank", "noopener,noreferrer");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not open document.");
    }
  }, [documentId, preferredPage, isPdf]);

  const iframeSrc = useMemo(() => {
    if (!signedUrl) return null;
    return preferredPage != null && isPdf
      ? `${signedUrl}#page=${preferredPage}`
      : signedUrl;
  }, [signedUrl, preferredPage, isPdf]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {/* Wide sheet so PDFs render at a readable size. shadcn default
          right-side variant caps at sm:max-w-sm; we override with a wider
          floor and let the 3/4 default carry beyond that. */}
      <SheetContent
        side="right"
        className="flex h-full w-full flex-col gap-0 p-0 sm:max-w-3xl"
      >
        <div className="flex items-center gap-2 border-b border-border p-4">
          <SheetTitle className="flex min-w-0 flex-1 items-center gap-2 truncate text-base">
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="truncate">{documentName ?? "Document preview"}</span>
          </SheetTitle>
          <button
            type="button"
            onClick={openInNewTab}
            className="ml-auto inline-flex shrink-0 items-center gap-1 text-xs font-medium text-primary hover:underline"
          >
            <ExternalLink className="h-3 w-3" /> Open in new tab
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden bg-muted/20">
          {loading && (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading preview…
            </div>
          )}

          {!loading && error && (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted-foreground">
              <p>{error}</p>
              <button
                type="button"
                onClick={openInNewTab}
                className="text-primary hover:underline"
              >
                Open the source file instead
              </button>
            </div>
          )}

          {!loading && !error && isPdf && iframeSrc && (
            <iframe
              key={iframeSrc}
              src={iframeSrc}
              title={documentName ?? "Document preview"}
              className="h-full w-full border-0"
            />
          )}

          {!loading && !error && !isPdf && chunks && (
            <ChunkText
              data={chunks}
              highlightPage={preferredPage ?? null}
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ChunkText({
  data,
  highlightPage,
}: {
  data: ChunksResponse;
  highlightPage: number | null;
}) {
  return (
    <div className="h-full overflow-y-auto px-6 py-4">
      <div className="mx-auto max-w-2xl space-y-4">
        {data.chunks.length === 0 ? (
          <p className="text-sm italic text-muted-foreground">
            This document has no extracted text yet.
          </p>
        ) : (
          data.chunks.map((c) => (
            <div
              key={c.chunk_index}
              className={cn(
                "rounded-md border border-transparent px-3 py-2 text-sm leading-6",
                highlightPage != null && c.page_number === highlightPage
                  ? "border-primary/40 bg-primary/5"
                  : "bg-background",
              )}
            >
              {(c.page_number != null || c.section_heading) && (
                <div className="mb-1 flex items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {c.page_number != null && <span>Page {c.page_number}</span>}
                  {c.section_heading && (
                    <span className="truncate">§ {c.section_heading}</span>
                  )}
                </div>
              )}
              <p className="whitespace-pre-wrap text-foreground/90">{c.content}</p>
            </div>
          ))
        )}
        {data.truncated && (
          <p className="rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            Preview truncated — open the source file to see the full document.
          </p>
        )}
      </div>
    </div>
  );
}
