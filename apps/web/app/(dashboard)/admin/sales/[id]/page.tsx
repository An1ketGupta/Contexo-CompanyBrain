"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  Copy,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  Send,
  X,
} from "lucide-react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatAbsolute, formatDistanceToNow } from "@/lib/date";
import { cn } from "@/lib/utils";
import type { Lead, SalesMessage } from "@/lib/types";

interface LeadDetail {
  lead: Lead;
  messages: SalesMessage[];
}

const KIND_LABELS: Record<string, string> = {
  first_touch: "First touch",
  follow_up: "Follow-up",
  reply: "Reply",
};

const fetcher = async (url: string): Promise<LeadDetail> => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (res.status === 404) throw new Error("Lead not found.");
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

export default function SalesLeadDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data, error, isLoading, mutate } = useSWR<LeadDetail>(
    `/api/admin/sales/${id}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const [draft, setDraft] = useState("");
  const [subject, setSubject] = useState("");
  const [replyBody, setReplyBody] = useState("");
  const [showReply, setShowReply] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const messages = data?.messages ?? [];
  const pendingDraft = [...messages]
    .reverse()
    .find((m) => m.author_type === "agent_draft" && m.status === "draft");
  const thread = messages.filter(
    (m) => m.direction === "inbound" || m.status?.includes("sent"),
  );

  // Seed the editor from the pending draft, but never clobber in-progress
  // edits once the rep has started typing.
  useEffect(() => {
    if (pendingDraft && draft === "") {
      setDraft(pendingDraft.body);
      setSubject(pendingDraft.subject ?? "");
    }
  }, [pendingDraft, draft]);

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
          href="/admin/sales"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to leads
        </Link>
        <div className="mt-4 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600 dark:text-red-400">
          {error?.message ?? "Failed to load lead."}
        </div>
      </div>
    );
  }

  const { lead } = data;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-5 p-6">
      <div>
        <Link
          href="/admin/sales"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to leads
        </Link>
        <h1 className="mt-2 text-xl font-extrabold tracking-tight">
          {lead.company_name}
        </h1>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span>
            {lead.contact_name ? `${lead.contact_name} · ` : ""}
            {lead.contact_email}
            {lead.contact_title ? ` · ${lead.contact_title}` : ""}
          </span>
          <span className="rounded-full border border-border bg-muted px-2 py-0.5 font-bold">
            {lead.status.replace(/_/g, " ")}
          </span>
          {lead.follow_up_count > 0 && (
            <span className="rounded-full border border-border bg-muted px-2 py-0.5">
              {lead.follow_up_count} sent
            </span>
          )}
          {lead.next_follow_up_at && (
            <span className="rounded-full border border-border bg-muted px-2 py-0.5">
              next follow-up {formatDistanceToNow(lead.next_follow_up_at)}
            </span>
          )}
        </div>
        {lead.escalation_reason && (
          <div className="mt-2 rounded-xl border border-amber/30 bg-amber-tint px-3 py-2 text-xs text-amber-ink">
            Held: {lead.escalation_reason.replace(/_/g, " ")}
          </div>
        )}
        {lead.context_note && (
          <div className="mt-2 rounded-xl border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
            <span className="font-bold">Your note:</span> {lead.context_note}
          </div>
        )}
      </div>

      {thread.length > 0 && (
        <section className="rounded-2xl border border-border bg-card p-4">
          <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            Conversation
          </div>
          <ul className="mt-2 space-y-3">
            {thread.map((m) => (
              <li
                key={m.id}
                className="border-t border-border pt-2 first:border-0 first:pt-0"
              >
                <div className="text-[11px] text-muted-foreground">
                  {m.direction === "inbound"
                    ? "They wrote"
                    : m.author_type === "human"
                      ? "Sent by a teammate"
                      : "Sent by agent"}{" "}
                  · {formatAbsolute(m.created_at)}
                  {m.subject ? ` · ${m.subject}` : ""}
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">
                  {m.body}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            {pendingDraft?.kind
              ? `Draft — ${KIND_LABELS[pendingDraft.kind] ?? pendingDraft.kind}`
              : "Draft"}
          </div>
          {pendingDraft?.confidence != null && (
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-bold",
                pendingDraft.confidence >= 0.65
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                  : "border-amber/30 bg-amber-tint text-amber-ink",
              )}
            >
              confidence {pendingDraft.confidence.toFixed(2)}
            </span>
          )}
        </div>

        <Input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Subject"
          className="mt-3"
        />

        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={12}
          className="mt-2 text-sm"
          placeholder="No draft generated yet."
        />

        {pendingDraft?.sources && pendingDraft.sources.length > 0 && (
          <div className="mt-3">
            <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
              Based on
            </div>
            <ul className="mt-1.5 space-y-1">
              {pendingDraft.sources.slice(0, 6).map((s, i) => (
                <li key={i} className="text-xs text-muted-foreground">
                  · {s.document_name ?? "Untitled document"}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy !== null || !draft.trim()}
            onClick={() =>
              run("send", async () => {
                await postAction(`/api/admin/sales/${id}/send`, {
                  body: draft,
                  subject: subject.trim() || undefined,
                });
                toast.success("Sent.");
              })
            }
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-xs font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {busy === "send" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
            Approve &amp; send
          </button>

          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              run("regenerate", async () => {
                await postAction(`/api/admin/sales/${id}/regenerate`);
                setDraft("");
                setSubject("");
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
              navigator.clipboard.writeText(
                subject ? `Subject: ${subject}\n\n${draft}` : draft,
              );
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
                await postAction(`/api/admin/sales/${id}/reject`, {});
                setDraft("");
                setSubject("");
                toast.success("Draft rejected.");
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

        <p className="mt-2 text-[11px] text-muted-foreground">
          Sending requires a rep with Gmail connected and send permission. Pick
          one under{" "}
          <Link href="/admin/sales/settings" className="underline">
            Sales settings
          </Link>
          , or use Copy to send from your own client.
        </p>
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
              Log a reply
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Replies don&apos;t arrive here automatically yet — paste what they
              wrote back. This also stops the follow-up cadence for this lead.
            </p>
          </div>
          {!showReply && (
            <button
              type="button"
              onClick={() => setShowReply(true)}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs font-bold transition-colors hover:bg-muted"
            >
              <MessageSquarePlus className="h-3.5 w-3.5" />
              Add
            </button>
          )}
        </div>

        {showReply && (
          <div className="mt-3 space-y-2">
            <Textarea
              value={replyBody}
              onChange={(e) => setReplyBody(e.target.value)}
              rows={5}
              className="text-sm"
              placeholder="Paste their reply here…"
            />
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={busy !== null || !replyBody.trim()}
                onClick={() =>
                  run("reply", async () => {
                    await postAction(`/api/admin/sales/${id}/reply`, {
                      body: replyBody,
                      draft_response: true,
                    });
                    setReplyBody("");
                    setShowReply(false);
                    setDraft("");
                    setSubject("");
                    toast.success("Reply logged — drafting an answer.");
                  })
                }
                className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-xs font-bold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busy === "reply" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : null}
                Log &amp; draft answer
              </button>
              <button
                type="button"
                disabled={busy !== null || !replyBody.trim()}
                onClick={() =>
                  run("reply-only", async () => {
                    await postAction(`/api/admin/sales/${id}/reply`, {
                      body: replyBody,
                      draft_response: false,
                    });
                    setReplyBody("");
                    setShowReply(false);
                    toast.success("Reply logged.");
                  })
                }
                className="rounded-full border border-border px-3.5 py-1.5 text-xs font-bold transition-colors hover:bg-muted disabled:opacity-50"
              >
                Log only
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowReply(false);
                  setReplyBody("");
                }}
                className="rounded-full px-3 py-1.5 text-xs font-bold text-muted-foreground transition-colors hover:text-foreground"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
