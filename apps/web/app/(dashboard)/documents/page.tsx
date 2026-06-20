"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { UploadDialog } from "@/components/documents/upload-dialog";
import { DOCUMENTS_REFRESH_EVENT } from "@/components/documents/upload-context";
import { DocumentCardList } from "@/components/documents/document-card-list";
import { DocumentTable } from "@/components/documents/document-table";
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

  return (
    <div className="mx-auto max-w-6xl p-6 md:p-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Documents
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
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
          <div className="hidden md:block">
            <DocumentTable
              documents={documents}
              selectedIds={selectedIds}
              onSelectionChange={setSelectedIds}
              onDelete={deleteDocument}
              onRetry={retryDocument}
              onUpdateTags={updateTags}
            />
          </div>
          <div className="md:hidden">
            <DocumentCardList
              documents={documents}
              onDelete={deleteDocument}
              onRetry={retryDocument}
            />
          </div>
        </>
      )}
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background">
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
    <div className="rounded-lg border border-dashed border-border bg-background px-6 py-16 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
        <FileText className="h-5 w-5" />
      </div>
      <h2 className="text-base font-semibold text-foreground">
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
    <div className="rounded-lg border border-dashed border-border bg-background px-6 py-12 text-center">
      <h2 className="text-base font-semibold text-foreground">No matches</h2>
      <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
        No documents match the current filters.
      </p>
      <button
        onClick={onClear}
        className="mt-3 text-sm font-medium text-primary hover:underline"
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
    <div className="rounded-lg border border-destructive/30 bg-destructive-soft/60 px-6 py-8 text-center">
      <p className="text-sm font-medium text-destructive">{message}</p>
      <button
        onClick={onRetry}
        className="mt-3 text-sm font-medium text-primary hover:underline"
      >
        Try again
      </button>
    </div>
  );
}
