"use client";

import { useEffect, useState } from "react";
import { use } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  Copy,
  Loader2,
  RefreshCw,
  Send,
  X,
} from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatAbsolute, formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";

interface Ticket {
  id: string;
  subject: string;
  from_email: string;
  from_name: string | null;
  status: string;
  category: string | null;
  priority: string | null;
  sentiment: string | null;
  escalation_reason: string | null;
  created_at: string;
}

interface SupportMessage {
  id: string;
  direction: "inbound" | "outbound";
  author_type: "customer" | "agent_draft" | "human" | "system";
  body: string;
  confidence: number | null;
  status: string | null;
  sent_via: string | null;
  created_at: string;
}

interface TicketDetail {
  ticket: Ticket;
  messages: SupportMessage[];
}

const fetcher = async (url: string): Promise<TicketDetail> => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (res.status === 404) throw new Error("Ticket not found.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

async function postAction(url: string, body?: unknown): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? data.message ?? `Request failed (${res.status})`);
  }
}

export default function SupportTicketDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data, error, isLoading, mutate } = useSWR<TicketDetail>(
    `/api/admin/support/${id}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const messages = data?.messages ?? [];
  const latestDraft = [...messages]
    .reverse()
    .find((m) => m.author_type === "agent_draft");
  const inbound = messages.find((m) => m.direction === "inbound");

  // Seed the editor from the latest draft, but never clobber in-progress edits
  // once the rep has started typing.
  useEffect(() => {
    if (latestDraft && draft === "") setDraft(latestDraft.body);
  }, [latestDraft, draft]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
      await mutate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full rounded-2xl" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="mx-auto w-full max-w-4xl p-6">
        <Link
          href="/admin/support"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to queue
        </Link>
        <div className="mt-4 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600 dark:text-red-400">
          {error?.message ?? "Failed to load ticket."}
        </div>
      </div>
    );
  }

  const { ticket } = data;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <div>
        <Link
          href="/admin/support"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to queue
        </Link>
        <h1 className="mt-2 text-xl font-extrabold tracking-tight">
          {ticket.subject}
        </h1>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span>
            {ticket.from_name ? `${ticket.from_name} · ` : ""}
            {ticket.from_email}
          </span>
          <span>·</span>
          <span>{formatDistanceToNow(ticket.created_at)}</span>
          <span className="rounded-full border border-border bg-muted px-2 py-0.5 font-bold">
            {ticket.status.replace(/_/g, " ")}
          </span>
          {ticket.priority && (
            <span className="rounded-full border border-border bg-muted px-2 py-0.5 font-bold uppercase">
              {ticket.priority}
            </span>
          )}
        </div>
        {ticket.escalation_reason && (
          <div className="mt-2 rounded-xl border border-amber/30 bg-amber-tint px-3 py-2 text-xs text-amber-ink">
            Escalated: {ticket.escalation_reason.replace(/_/g, " ")}
          </div>
        )}
      </div>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Customer message
        </div>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
          {inbound?.body ?? "(no inbound body recorded)"}
        </p>
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            Draft reply
          </div>
          {latestDraft?.confidence != null && (
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-bold",
                latestDraft.confidence >= 0.65
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                  : "border-amber/30 bg-amber-tint text-amber-ink",
              )}
            >
              confidence {latestDraft.confidence.toFixed(2)}
            </span>
          )}
        </div>

        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={12}
          className="mt-3 text-sm"
          placeholder="No draft generated yet."
        />

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy !== null || !draft.trim()}
            onClick={() =>
              run("send", async () => {
                await postAction(`/api/admin/support/${id}/send`, { body: draft });
                toast.success("Reply sent.");
              })
            }
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-xs font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy === "send" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
            Send reply
          </button>

          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              run("regenerate", async () => {
                await postAction(`/api/admin/support/${id}/regenerate`);
                toast.success("Redrafting — refresh in a moment.");
              })
            }
            className="inline-flex items-center gap-1.5 rounded-full border border-border px-3.5 py-1.5 text-xs font-bold transition-colors hover:bg-muted disabled:opacity-50"
          >
            {busy === "regenerate" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Regenerate
          </button>

          <button
            type="button"
            disabled={busy !== null || !draft.trim()}
            onClick={() => {
              navigator.clipboard.writeText(draft);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-border px-3.5 py-1.5 text-xs font-bold transition-colors hover:bg-muted disabled:opacity-50"
            title="Copy the draft to send manually from your own mail client"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            Copy
          </button>

          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              run("reject", async () => {
                await postAction(`/api/admin/support/${id}/reject`, {});
                toast.success("Marked for manual handling.");
              })
            }
            className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-border px-3.5 py-1.5 text-xs font-bold text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            {busy === "reject" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <X className="h-3.5 w-3.5" />
            )}
            Reject
          </button>
        </div>
      </section>

      {messages.filter((m) => m.direction === "outbound" && m.status?.includes("sent"))
        .length > 0 && (
        <section className="rounded-2xl border border-border bg-card p-4">
          <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            Sent history
          </div>
          <ul className="mt-2 space-y-3">
            {messages
              .filter((m) => m.direction === "outbound" && m.status?.includes("sent"))
              .map((m) => (
                <li key={m.id} className="border-t border-border pt-2 first:border-0 first:pt-0">
                  <div className="text-[11px] text-muted-foreground">
                    {m.author_type === "human" ? "Sent by a teammate" : "Sent by agent"} ·{" "}
                    {formatAbsolute(m.created_at)}
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-sm">{m.body}</p>
                </li>
              ))}
          </ul>
        </section>
      )}
    </div>
  );
}
