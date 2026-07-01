"use client";

import useSWR from "swr";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDistanceToNow } from "@/lib/date";

interface DocumentVersion {
  id: string;
  document_id: string;
  version_number: number;
  file_size_bytes: number | null;
  uploaded_by: string | null;
  uploaded_by_name: string | null;
  is_current: boolean;
  created_at: string;
}

interface VersionsResponse {
  versions: DocumentVersion[];
}

const fetcher = async (url: string): Promise<VersionsResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load versions (${res.status})`);
  return res.json();
};

export function DocumentVersionHistory({
  documentId,
}: {
  documentId: string;
}) {
  const { data, isLoading } = useSWR<VersionsResponse>(
    `/api/documents/${documentId}/versions`,
    fetcher,
    { revalidateOnFocus: false },
  );

  if (isLoading) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading version history…
      </p>
    );
  }

  const versions = data?.versions ?? [];
  if (versions.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No version records yet.
      </p>
    );
  }

  return (
    <ul className="space-y-1.5">
      {versions.map((v) => (
        <li key={v.id} className="flex items-center gap-2 text-xs">
          <span className="inline-flex h-5 items-center rounded bg-muted px-1.5 font-mono text-[11px] text-foreground">
            v{v.version_number}
          </span>
          <span className="text-muted-foreground">
            uploaded by {v.uploaded_by_name ?? "someone"} ·{" "}
            {formatDistanceToNow(v.created_at)}
          </span>
          {v.is_current ? (
            <Badge variant="outline" className="ml-auto">
              Current
            </Badge>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
