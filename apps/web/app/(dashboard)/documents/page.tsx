"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { UploadDialog } from "@/components/documents/upload-dialog";
import { DOCUMENTS_REFRESH_EVENT } from "@/components/documents/upload-context";
import { DocumentCardList } from "@/components/documents/document-card-list";
import { DocumentTable, isMeetingTranscript } from "@/components/documents/document-table";
import { DocumentFiltersBar } from "@/components/documents/document-filters";
import { BulkActionBar } from "@/components/documents/bulk-action-bar";
import { RecommendationsWidget } from "@/components/documents/recommendations-widget";
import { refreshRecommendations } from "@/hooks/use-recommendations";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DEFAULT_FILTERS,
  isFiltering,
  useDocuments,
  type DocumentFilters,
} from "@/hooks/use-documents";
import { useDocumentsRealtime } from "@/hooks/use-documents-realtime";

export default function DocumentsPage() {
  const [filters, setFilters] = useState<DocumentFilters>(DEFAULT_FILTERS);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const {
    documents,
    total,
    loading,
    error,
    refresh,
    deleteDocument,
    bulkDelete,
    bulkAddTags,
    updateTags,
    updateVisibility,
    upsertDocument,
    removeDocument,
    retryDocument,
  } = useDocuments(filters);

  useDocumentsRealtime({
    onUpsert: upsertDocument,
    onRemove: removeDocument,
    // The dashboard layout owns the global "ready" toast (V4 #60) so we
    // mute it here to avoid double-firing on the documents page.
    silentToasts: true,
  });

  // Background uploads (kicked off from this page or any other dashboard
  // route) dispatch `documents:refresh` after each successful complete call.
  // Realtime usually beats this to the punch, but the explicit refetch keeps
  // the row visible immediately even if the Realtime channel is reconnecting.
  useEffect(() => {
    const handler = () => {
      refresh();
      // Upload may have auto-matched a recommendation — refetch the widget.
      refreshRecommendations();
    };
    window.addEventListener(DOCUMENTS_REFRESH_EVENT, handler);
    return () => window.removeEventListener(DOCUMENTS_REFRESH_EVENT, handler);
  }, [refresh]);

  const filtering = isFiltering(filters);
  const isEmpty = !loading && !error && documents.length === 0;
  const meetingDocuments = documents.filter(isMeetingTranscript);
  const otherDocuments = documents.filter((d) => !isMeetingTranscript(d));

  return (
    <div className="mx-auto max-w-6xl p-6 md:p-8">
      <div className="mb-7 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground">
            Documents
          </h1>
          <p className="mt-1.5 text-[15px] leading-relaxed text-muted-foreground">
            Everything your AI knows about your company.
          </p>
        </div>
        <UploadDialog />
      </div>

      <RecommendationsWidget />

      <DocumentFiltersBar
        filters={filters}
        onChange={setFilters}
        totalShown={documents.length}
        totalAvailable={total}
      />

      <BulkActionBar
        selectedIds={selectedIds}
        onClear={() => setSelectedIds(new Set())}
        onBulkDelete={bulkDelete}
        onBulkAddTags={bulkAddTags}
      />

      {loading && documents.length === 0 ? (
        <TableSkeleton />
      ) : error ? (
        <ErrorState message={error} onRetry={refresh} />
      ) : isEmpty ? (
        filtering ? <NoMatchesState onClear={() => setFilters(DEFAULT_FILTERS)} /> : <EmptyState />
      ) : (
        <>
          {meetingDocuments.length > 0 && (
            <div className="mb-8">
              <h2 className="mb-3 text-sm font-bold uppercase tracking-wide text-muted-foreground">
                Meeting
              </h2>
              <div className="hidden md:block">
                <DocumentTable
                  documents={meetingDocuments}
                  selectedIds={selectedIds}
                  onSelectionChange={setSelectedIds}
                  onDelete={deleteDocument}
                  onRetry={retryDocument}
                  onUpdateTags={updateTags}
                  onUpdateVisibility={updateVisibility}
                  onRefresh={refresh}
                  hideVersioning
                  hideTags
                />
              </div>
              <div className="md:hidden">
                <DocumentCardList
                  documents={meetingDocuments}
                  onDelete={deleteDocument}
                  onRetry={retryDocument}
                  hideVersioning
                  hideTags
                />
              </div>
            </div>
          )}

          {otherDocuments.length > 0 && (
            <div className="hidden md:block">
              <DocumentTable
                documents={otherDocuments}
                selectedIds={selectedIds}
                onSelectionChange={setSelectedIds}
                onDelete={deleteDocument}
                onRetry={retryDocument}
                onUpdateTags={updateTags}
                onUpdateVisibility={updateVisibility}
                onRefresh={refresh}
              />
            </div>
          )}
          {otherDocuments.length > 0 && (
            <div className="md:hidden">
              <DocumentCardList
                documents={otherDocuments}
                onDelete={deleteDocument}
                onRetry={retryDocument}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-background">
      <div className="border-b border-border bg-muted/40 px-4 py-2.5">
        <Skeleton className="h-3 w-20" />
      </div>
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
        >
          <Skeleton className="h-4 w-4" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-2.5 w-1/4" />
          </div>
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
      ))}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-background px-6 py-16 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-tint text-brand">
        <FileText className="h-5 w-5" />
      </div>
      <h2 className="text-base font-bold text-foreground">
        No documents yet
      </h2>
      <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
        Upload a PDF, DOCX, TXT, or MD file. Once processed, your AI will use it
        as context for every task.
      </p>
    </div>
  );
}

function NoMatchesState({ onClear }: { onClear: () => void }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-background px-6 py-12 text-center">
      <h2 className="text-base font-bold text-foreground">No matches</h2>
      <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
        No documents match the current filters.
      </p>
      <button
        onClick={onClear}
        className="mt-3 text-sm font-semibold text-brand hover:underline"
      >
        Clear filters
      </button>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-2xl border border-destructive/30 bg-destructive-soft/60 px-6 py-8 text-center">
      <p className="text-sm font-medium text-destructive">{message}</p>
      <button
        onClick={onRetry}
        className="mt-3 text-sm font-semibold text-brand hover:underline"
      >
        Try again
      </button>
    </div>
  );
}
