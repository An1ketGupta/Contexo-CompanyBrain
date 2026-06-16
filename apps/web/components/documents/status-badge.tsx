"use client";

import { AlertTriangle, CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { DocumentStatus } from "@/lib/types";

interface StatusBadgeProps {
  status: DocumentStatus;
  embeddingStats?: { embedded: number; failed: number; total: number } | null;
  errorReason?: string | null;
}

export function StatusBadge({ status, embeddingStats, errorReason }: StatusBadgeProps) {
  // A "ready" doc with non-zero failed chunks is partial — surface it with an
  // amber warning so users notice the missing context before they complain.
  if (
    status === "ready" &&
    embeddingStats &&
    embeddingStats.failed > 0 &&
    embeddingStats.embedded > 0
  ) {
    return (
      <Badge
        variant="outline"
        className="border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-300"
        title={`${embeddingStats.embedded} of ${embeddingStats.total} chunks embedded.`}
      >
        <AlertTriangle />
        Partial ({embeddingStats.embedded}/{embeddingStats.total})
      </Badge>
    );
  }

  switch (status) {
    case "pending":
      return (
        <Badge variant="outline">
          <Clock />
          Pending
        </Badge>
      );
    case "processing":
      return (
        <Badge variant="accent">
          <Loader2 className="animate-spin" />
          Processing
        </Badge>
      );
    case "ready":
      return (
        <Badge variant="success">
          <CheckCircle2 />
          Ready
        </Badge>
      );
    case "failed":
      return (
        <Badge variant="destructive" title={errorReason || undefined}>
          <XCircle />
          Failed
        </Badge>
      );
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}
