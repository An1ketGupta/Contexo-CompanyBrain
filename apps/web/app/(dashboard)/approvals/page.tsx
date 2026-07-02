"use client";

import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  Clock,
  Loader2,
  Mail,
  MessageSquare,
  Newspaper,
  FileText as FileTextIcon,
  XCircle,
} from "lucide-react";
import useSWR from "swr";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "@/lib/date";

type Tab = "inbox" | "sent";
type StatusFilter = "pending" | "approved" | "rejected" | "all";
type Action = "approved" | "rejected";

interface Approval {
  id: string;
  status: string;
  note: string | null;
  preview_text: string | null;
  execution_action: { channel: string; params: Record<string, unknown> };
  requested_by: string;
  approver_id: string;
  requested_by_name: string | null;
  approver_name: string | null;
  message_id: string;
  created_at: string;
  resolved_at: string | null;
  resolved_via: string | null;
  token_expires_at: string;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

const CHANNEL_META: Record<string, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  gmail: { label: "Send via Gmail", icon: Mail },
  slack: { label: "Post to Slack", icon: MessageSquare },
  notion: { label: "Create Notion page", icon: Newspaper },
  gdocs: { label: "Create Google Doc", icon: FileTextIcon },
};

function destinationOf(a: Approval): string | null {
  const ch = a.execution_action?.channel;
  const p = (a.execution_action?.params ?? {}) as Record<string, string | undefined>;
  if (ch === "gmail") return p.to ?? null;
  if (ch === "slack") return p.channel_name ?? p.channel_id ?? null;
  if (ch === "notion" || ch === "gdocs") return p.title ?? null;
  return null;
}

export default function ApprovalsPage() {
  const [tab, setTab] = useState<Tab>("inbox");
  const [status, setStatus] = useState<StatusFilter>("pending");
  const url = `/api/approvals?tab=${tab}&status=${status}&limit=50`;
  const { data, error, isLoading, mutate } = useSWR<{ approvals: Approval[] }>(url, fetcher);

  const [active, setActive] = useState<Approval | null>(null);
  const [resolveBusy, setResolveBusy] = useState<Action | null>(null);
  const [note, setNote] = useState("");

  const counts = useMemo(() => {
    const items = data?.approvals ?? [];
    return {
      total: items.length,
    };
  }, [data]);

  async function resolve(action: Action) {
    if (!active) return;
    setResolveBusy(action);
    try {
      const res = await fetch(`/api/approvals/${active.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: note.trim() || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      toast.success(action === "approved" ? "Approved" : "Rejected");
      setActive(null);
      setNote("");
      mutate();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Resolution failed");
    } finally {
      setResolveBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="mb-6">
        <h1 className="text-3xl font-extrabold tracking-tight">Approvals</h1>
        <p className="mt-1.5 text-[15px] text-muted-foreground">
          Review and approve AI outputs before they go out.
        </p>
      </header>

      {/* Stack the inbox/sent tabs and status filter on mobile — at 375px
          they don't fit side-by-side without the filter chips wrapping in
          a way that obscures the tab borders. */}
      <div className="mb-4 border-b">
        <div className="flex items-center gap-2">
          <TabButton active={tab === "inbox"} onClick={() => setTab("inbox")}>
            Inbox
          </TabButton>
          <TabButton active={tab === "sent"} onClick={() => setTab("sent")}>
            Sent
          </TabButton>
        </div>
        <div className="-mx-1 flex items-center gap-1 overflow-x-auto pb-2 pt-1 sm:mx-0 sm:gap-2 sm:pt-0 sm:[scrollbar-width:none]">
          {(["pending", "approved", "rejected", "all"] as StatusFilter[]).map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={cn(
                "shrink-0 rounded-full px-3 py-1 text-xs font-medium capitalize transition-colors",
                status === s
                  ? "bg-brand-tint font-semibold text-brand"
                  : "text-muted-foreground hover:bg-muted/50",
              )}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <ApprovalsListSkeleton />
      ) : error ? (
        <div className="rounded-2xl border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive-ink">
          Couldn&apos;t load approvals. Refresh to try again.
        </div>
      ) : counts.total === 0 ? (
        <EmptyState tab={tab} status={status} />
      ) : (
        <ul className="divide-y rounded-2xl border bg-card">
          {(data?.approvals ?? []).map((a) => (
            <ApprovalRow
              key={a.id}
              approval={a}
              tab={tab}
              onClick={() => setActive(a)}
            />
          ))}
        </ul>
      )}

      <Dialog open={Boolean(active)} onOpenChange={(o) => !o && setActive(null)}>
        {active ? (
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>
                {tab === "inbox" ? "Review approval" : "Your request"}
              </DialogTitle>
              <DialogDescription>
                {CHANNEL_META[active.execution_action?.channel ?? ""]?.label ??
                  active.execution_action?.channel}{" "}
                {destinationOf(active) ? ` → ${destinationOf(active)}` : null}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-3">
              <div className="rounded-xl border bg-muted/40 p-3 text-sm">
                <div className="mb-2 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                  Draft
                </div>
                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap font-sans text-[13px] leading-relaxed">
                  {active.preview_text ?? "No preview available."}
                </pre>
              </div>

              {tab === "inbox" && active.status === "pending" ? (
                <div>
                  <label className="mb-1.5 block font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                    Note (optional)
                  </label>
                  <Textarea
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="Leave a note for the requester…"
                    rows={3}
                  />
                </div>
              ) : active.note ? (
                <div className="rounded-xl border bg-muted/40 p-3 text-sm">
                  <div className="mb-1 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                    Note from {active.approver_name ?? "approver"}
                  </div>
                  <p className="whitespace-pre-wrap text-[13px]">{active.note}</p>
                </div>
              ) : null}
            </div>

            <DialogFooter>
              {tab === "inbox" && active.status === "pending" ? (
                <>
                  <Button
                    variant="outline"
                    disabled={Boolean(resolveBusy)}
                    onClick={() => resolve("rejected")}
                  >
                    {resolveBusy === "rejected" ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <XCircle className="mr-2 h-4 w-4" />
                    )}
                    Reject
                  </Button>
                  <Button
                    disabled={Boolean(resolveBusy)}
                    onClick={() => resolve("approved")}
                  >
                    {resolveBusy === "approved" ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <CheckCircle2 className="mr-2 h-4 w-4" />
                    )}
                    Approve
                  </Button>
                </>
              ) : (
                <Button variant="outline" onClick={() => setActive(null)}>
                  Close
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "border-foreground text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function ApprovalRow({
  approval,
  tab,
  onClick,
}: {
  approval: Approval;
  tab: Tab;
  onClick: () => void;
}) {
  const meta = CHANNEL_META[approval.execution_action?.channel ?? ""] ?? {
    label: approval.execution_action?.channel ?? "Unknown",
    icon: FileTextIcon,
  };
  const Icon = meta.icon;
  const counterparty =
    tab === "inbox" ? approval.requested_by_name : approval.approver_name;
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40"
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-tint">
          <Icon className="h-4 w-4 text-brand" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">
              {meta.label}
              {destinationOf(approval)
                ? ` → ${destinationOf(approval)}`
                : ""}
            </span>
            <StatusBadge status={approval.status} />
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {tab === "inbox" ? "From " : "To "}
            <span className="font-medium text-foreground/80">
              {counterparty ?? "—"}
            </span>
            <span className="px-1.5">·</span>
            <Clock className="mr-1 inline h-3 w-3 align-[-2px]" />
            {formatDistanceToNow(approval.created_at)}
          </div>
        </div>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
      </button>
    </li>
  );
}

type StatusVariant = "warning" | "success" | "destructive" | "default";

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; variant: StatusVariant }> = {
    pending: { label: "Pending", variant: "warning" },
    approved: { label: "Approved", variant: "success" },
    executed: { label: "Executed", variant: "success" },
    rejected: { label: "Rejected", variant: "destructive" },
    execution_failed: { label: "Failed", variant: "destructive" },
    expired: { label: "Expired", variant: "default" },
  };
  const m = map[status] ?? { label: status, variant: "default" as StatusVariant };
  return (
    <Badge variant={m.variant} className="h-5 px-2 text-[10px]">
      {m.label}
    </Badge>
  );
}

function ApprovalsListSkeleton() {
  return (
    <ul className="divide-y rounded-2xl border bg-card">
      {Array.from({ length: 4 }).map((_, i) => (
        <li key={i} className="flex items-center gap-3 px-4 py-3">
          <Skeleton className="h-9 w-9 shrink-0 rounded-xl" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <div className="flex items-center gap-2">
              <Skeleton
                className="h-3.5"
                style={{ width: `${40 + ((i * 11) % 30)}%` }}
              />
              <Skeleton className="h-4 w-16 rounded-full" />
            </div>
            <Skeleton className="h-2.5 w-2/5" />
          </div>
          <Skeleton className="h-4 w-4 rounded" />
        </li>
      ))}
    </ul>
  );
}

function EmptyState({ tab, status }: { tab: Tab; status: StatusFilter }) {
  const message =
    tab === "inbox"
      ? status === "pending"
        ? "No requests waiting on you."
        : "Nothing matches that filter."
      : status === "pending"
        ? "You haven't submitted anything for approval yet."
        : "Nothing matches that filter.";
  return (
    <div className="rounded-2xl border bg-card px-6 py-12 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-tint text-brand">
        <CheckCircle2 className="h-5 w-5" />
      </div>
      <p className="mt-3 text-sm text-muted-foreground">{message}</p>
    </div>
  );
}
