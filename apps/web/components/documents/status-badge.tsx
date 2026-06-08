"use client";

import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { DocumentStatus } from "@/lib/types";

interface StatusBadgeProps {
  status: DocumentStatus;
}

export function StatusBadge({ status }: StatusBadgeProps) {
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
        <Badge variant="destructive">
          <XCircle />
          Failed
        </Badge>
      );
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}
