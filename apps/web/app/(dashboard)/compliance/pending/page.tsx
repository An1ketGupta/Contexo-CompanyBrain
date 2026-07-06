"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, FileText, Loader2, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface PendingItem {
  id: string;
  document_id: string;
  status: string;
  created_at: string;
  documents?: {
    id: string;
    name: string;
    file_type?: string;
    tags?: string[];
  } | null;
  document_versions?: {
    version_number?: number;
    created_at?: string;
  } | null;
  document_diffs?: {
    diff_summary?: string;
    from_version?: number;
    to_version?: number;
  } | null;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function CompliancePendingPage() {
  const { data, mutate, isLoading } = useSWR<{ pending: PendingItem[] }>(
    "/api/compliance/my-pending",
    fetcher,
    { revalidateOnFocus: true },
  );
  const [busy, setBusy] = useState<string | null>(null);

  const pending = data?.pending ?? [];

  async function acknowledge(documentId: string) {
    setBusy(documentId);
    try {
      const res = await fetch(`/api/compliance/${documentId}/acknowledge`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(body.message ?? body.detail ?? "Couldn't record acknowledgement.");
        return;
      }
      toast.success("Acknowledged. Thanks.");
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  async function dismiss(documentId: string) {
    setBusy(documentId);
    try {
      const res = await fetch(`/api/compliance/${documentId}/dismiss`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        toast.error("Couldn't dismiss this item.");
        return;
      }
      toast.success("Dismissed. Your admin will see this in their report.");
      await mutate();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 md:px-8">
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-blue-600" />
            <h1 className="text-2xl font-semibold tracking-tight">
              Policy acknowledgements
            </h1>
          </div>
          <p className="text-sm text-muted-foreground">
            Review what changed and confirm you&apos;ve read each update. Your
            admin sees who has and hasn&apos;t acknowledged.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : pending.length === 0 ? (
        <div className="rounded-lg border bg-card p-10 text-center">
          <ShieldCheck className="mx-auto mb-3 h-8 w-8 text-emerald-600" />
          <h2 className="mb-1 text-base font-medium">You&apos;re all caught up</h2>
          <p className="text-sm text-muted-foreground">
            No policy documents are pending your acknowledgement.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {pending.map((item) => (
            <PendingCard
              key={item.id}
              item={item}
              busy={busy === item.document_id}
              onAcknowledge={() => acknowledge(item.document_id)}
              onDismiss={() => dismiss(item.document_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PendingCard({
  item,
  busy,
  onAcknowledge,
  onDismiss,
}: {
  item: PendingItem;
  busy: boolean;
  onAcknowledge: () => void;
  onDismiss: () => void;
}) {
  const doc = item.documents;
  const diff = item.document_diffs?.diff_summary;
  const version = item.document_versions?.version_number;
  return (
    <div className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-2">
            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
            <h3 className="truncate text-base font-medium">
              {doc?.name ?? "Untitled document"}
            </h3>
            {version != null && (
              <Badge variant="accent" className="shrink-0">
                v{version}
              </Badge>
            )}
          </div>
          {doc?.id && (
            <Link
              href={`/documents/${doc.id}`}
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
            >
              Open document
            </Link>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={onDismiss}
          >
            <X className="mr-1 h-3.5 w-3.5" /> Doesn&apos;t apply
          </Button>
          <Button
            size="sm"
            disabled={busy}
            onClick={onAcknowledge}
          >
            {busy ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="mr-1 h-3.5 w-3.5" />
            )}
            Acknowledge
          </Button>
        </div>
      </div>

      {diff ? (
        <div className="rounded-md border bg-muted/40 p-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            What changed
          </p>
          <pre className="whitespace-pre-wrap text-sm text-foreground/90">
            {diff}
          </pre>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          New policy document. Open it to read before acknowledging.
        </p>
      )}
    </div>
  );
}
