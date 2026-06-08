"use client";

import { formatDistanceToNow } from "@/lib/date";
import type { Document } from "@/lib/types";
import { FileIcon } from "./file-icon";
import { StatusBadge } from "./status-badge";
import { DeleteDocumentDialog } from "./delete-document-dialog";

interface DocumentTableProps {
  documents: Document[];
  onDelete: (id: string) => Promise<void>;
}

function formatSize(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function DocumentTable({ documents, onDelete }: DocumentTableProps) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background">
      <table className="w-full text-sm">
        <thead className="border-b border-border bg-muted/40 text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 text-left font-medium">Name</th>
            <th className="hidden px-4 py-2.5 text-left font-medium md:table-cell">
              Size
            </th>
            <th className="hidden px-4 py-2.5 text-left font-medium lg:table-cell">
              Uploaded
            </th>
            <th className="px-4 py-2.5 text-left font-medium">Status</th>
            <th className="px-4 py-2.5 text-right font-medium">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {documents.map((doc) => (
            <tr key={doc.id} className="transition-colors hover:bg-muted/40">
              <td className="px-4 py-3">
                <div className="flex items-center gap-3 min-w-0">
                  <FileIcon
                    type={doc.file_type}
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                  />
                  <div className="min-w-0">
                    <p className="truncate font-medium text-foreground">
                      {doc.name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground md:hidden">
                      {doc.file_type.toUpperCase()} · {formatSize(doc.file_size_bytes)}
                    </p>
                  </div>
                </div>
              </td>
              <td className="hidden px-4 py-3 text-muted-foreground md:table-cell">
                {formatSize(doc.file_size_bytes)}
              </td>
              <td className="hidden px-4 py-3 text-muted-foreground lg:table-cell">
                {formatDistanceToNow(doc.created_at)}
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={doc.status} />
              </td>
              <td className="px-4 py-3 text-right">
                <DeleteDocumentDialog document={doc} onConfirm={onDelete} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
