"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ChevronRight,
  Inbox,
  Loader2,
  MailCheck,
  MailX,
  Sparkles,
} from "lucide-react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "@/lib/date";

interface TicketRow {
  id: string;
  from_email: string;
  from_name: string | null;
  subject: string;
  category: string;
  status: string;
  classifier_confidence: number | null;
  ai_draft_confidence: number | null;
  has_draft: boolean;
  agent_run_id: string | null;
  created_at: string;
  resolved_at: string | null;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_TABS: Array<{ key: string | null; label: string }> = [
  { key: null, label: "All" },
  { key: "pending", label: "Pending" },
  { key: "drafted", label: "Drafted" },
  { key: "sent", label: "Sent" },
  { key: "rejected", label: "Rejected" },
  { key: "drafting_failed", label: "Failed" },
];

const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: "Pending", cls: "bg-amber-tint text-amber-ink" },
  drafted: { label: "Draft ready", cls: "bg-brand-tint text-brand" },
  sent: { label: "Sent", cls: "bg-success-tint text-success-ink" },
  rejected: { label: "Rejected", cls: "bg-muted text-muted-foreground" },
  drafting_failed: { label: "Failed", cls: "bg-destructive-soft text-destructive-ink" },
  no_draft: { label: "Skipped (low conf.)", cls: "bg-muted text-muted-foreground" },
};

function confidenceBadge(score: number | null | undefined) {
  if (score === null || score === undefined) return null;
  const tone =
    score >= 7
      ? "bg-success-tint text-success-ink"
      : score >= 4
        ? "bg-amber-tint text-amber-ink"
        : "bg-destructive-soft text-destructive-ink";
  return (
    <Badge variant="outline" className={cn("font-mono text-[10px]", tone)}>
      <Sparkles className="mr-1 h-3 w-3" />
      {score.toFixed(1)}/10
    </Badge>
  );
}

export default function SupportQueuePage() {
  const [statusFilter, setStatusFilter] = useState<string | null>(null);

  const listUrl = useMemo(() => {
    const sp = new URLSearchParams();
    if (statusFilter) sp.set("status", statusFilter);
    sp.set("limit", "100");
    return `/api/admin/support?${sp.toString()}`;
  }, [statusFilter]);

  const { data, error, isLoading } = useSWR<{ tickets: TicketRow[]; total: number }>(
    listUrl,
    fetcher,
    { refreshInterval: 15_000 },
  );

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-extrabold tracking-tight">
            <Inbox className="h-6 w-6 text-brand" />
            Support inbox
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Inbound emails classified as support or sales. The AI drafts a reply
            using your knowledge base — review, edit if needed, then send via Gmail.
          </p>
        </div>
      </header>

      <div className="mb-4 flex flex-wrap gap-2">
        {STATUS_TABS.map((tab) => (
          <Button
            key={tab.label}
            size="sm"
            variant={statusFilter === tab.key ? "primary" : "outline"}
            onClick={() => setStatusFilter(tab.key)}
            className="rounded-full"
          >
            {tab.label}
          </Button>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive-soft px-3 py-2 text-sm text-destructive-ink">
          <AlertCircle className="h-4 w-4" />
          {String(error.message || error)}
        </div>
      )}

      {isLoading && !data && (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full rounded-2xl" />
          ))}
        </div>
      )}

      {data && data.tickets.length === 0 && (
        <div className="rounded-2xl border border-dashed border-border py-12 text-center text-sm text-muted-foreground">
          <Inbox className="mx-auto mb-2 h-8 w-8 opacity-50" />
          No tickets yet. Inbound emails to your org's address will appear here once classified.
        </div>
      )}

      {data && data.tickets.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-border">
          <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-muted/60 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left">From</th>
                <th className="px-3 py-2 text-left">Subject</th>
                <th className="px-3 py-2 text-left">Category</th>
                <th className="px-3 py-2 text-left">AI confidence</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.tickets.map((t) => {
                const meta = STATUS_META[t.status] ?? { label: t.status, cls: "bg-zinc-500/10 text-zinc-600" };
                return (
                  <tr key={t.id} className="border-t hover:bg-muted/30">
                    <td className="px-3 py-2">
                      <div className="font-medium">{t.from_name || t.from_email}</div>
                      {t.from_name && (
                        <div className="text-xs text-muted-foreground">{t.from_email}</div>
                      )}
                    </td>
                    <td className="px-3 py-2 max-w-[280px] truncate">{t.subject || "(no subject)"}</td>
                    <td className="px-3 py-2">
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {t.category}
                      </Badge>
                    </td>
                    <td className="px-3 py-2">{confidenceBadge(t.ai_draft_confidence)}</td>
                    <td className="px-3 py-2">
                      <Badge variant="outline" className={cn("font-mono text-[10px]", meta.cls)}>
                        {t.status === "pending" ? (
                          <>
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            {meta.label}
                          </>
                        ) : t.status === "sent" ? (
                          <>
                            <MailCheck className="mr-1 h-3 w-3" />
                            {meta.label}
                          </>
                        ) : t.status === "rejected" ? (
                          <>
                            <MailX className="mr-1 h-3 w-3" />
                            {meta.label}
                          </>
                        ) : (
                          meta.label
                        )}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {formatDistanceToNow(new Date(t.created_at))} ago
                    </td>
                    <td className="px-3 py-2">
                      <Link
                        href={`/admin/support/${t.id}`}
                        className="inline-flex items-center text-sm font-bold text-brand hover:underline"
                      >
                        Review <ChevronRight className="h-3 w-3" />
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}
