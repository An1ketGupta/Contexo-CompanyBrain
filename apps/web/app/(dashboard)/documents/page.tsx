"use client";

import { UploadDialog } from "@/components/documents/upload-dialog";
import { useDocuments } from "@/hooks/use-documents";

export default function DocumentsPage() {
  const { documents, loading, refresh } = useDocuments();

  return (
    <main className="p-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-foreground">Documents</h1>
        <UploadDialog onUploadComplete={refresh} />
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No documents yet. Upload your first document to get started.
        </p>
      ) : (
        <div className="space-y-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center justify-between rounded-md border border-border bg-background px-4 py-3"
            >
              <div>
                <p className="text-sm font-medium text-foreground">{doc.name}</p>
                <p className="text-xs text-muted-foreground">
                  {doc.file_type.toUpperCase()} ·{" "}
                  {doc.file_size_bytes
                    ? `${(doc.file_size_bytes / 1024 / 1024).toFixed(2)} MB · `
                    : ""}
                  {doc.status}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
