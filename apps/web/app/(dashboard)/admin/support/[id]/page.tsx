"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  Mail,
  RefreshCw,
  Send,
  Sparkles,
  XCircle,
} from "lucide-react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface TicketSource {
  document_id?: string;
  document_name?: string;
  page_number?: number | null;
  excerpt?: string;
  similarity?: number;
}

interface TicketDetail {
  id: string;
  from_email: string;
  from_name: string | null;
  subject: string;
  body: string;
  category: string;
  status: string;
  classifier_confidence: number | null;
  classifier_reason: string | null;
  ai_draft_body: string | null;
  ai_draft_sources: TicketSource[] | null;
  ai_draft_confidence: number | null;
  ai_draft_message_id: string | null;
  agent_run_id: string | null;
  inbound_sig: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
  delivery_status?: Record<string, unknown> | null;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function SupportTicketDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();

  const { data, error, isLoading, mutate } = useSWR<{ ticket: TicketDetail }>(
    `/api/admin/support/${id}`,
    fetcher,
    { refreshInterval: 8_000 },
  );
  const ticket = data?.ticket;

  const [editedBody, setEditedBody] = useState<string>("");
  const [subject, setSubject] = useState<string>("");
  const [busy, setBusy] = useState<null | "send" | "regenerate" | "reject">(null);
  const [bannerError, setBannerError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Keep the textarea in sync when fresh data arrives, but don't clobber
  // user edits in flight: only reset when the AI draft body actually changes.
  useEffect(() => {
    if (!ticket) return;
    setEditedBody((prev) =>
      prev && prev !== (ticket.ai_draft_body ?? "") ? prev : ticket.ai_draft_body ?? "",
    );
    setSubject((prev) => prev || `Re: ${ticket.subject || ""}`);
  }, [ticket?.id, ticket?.ai_draft_body]);  // eslint-disable-line react-hooks/exhaustive-deps

  const canSend = useMemo(() => {
    if (!ticket) return false;
    if (ticket.status === "sent") return false;
    return Boolean(editedBody.trim() && subject.trim() && ticket.from_email);
  }, [ticket, editedBody, subject]);

  async function doAction(action: "send" | "regenerate" | "reject") {
    if (!ticket || busy) return;
    setBusy(action);
    setBannerError(null);
    setSuccessMsg(null);
    try {
      const url = `/api/admin/support/${ticket.id}/${action}`;
      const init: RequestInit = { method: "POST", headers: { "Content-Type": "application/json" } };
      if (action === "send") {
        init.body = JSON.stringify({ body: editedBody, subject });
      } else if (action === "reject") {
        init.body = JSON.stringify({ note: null });
      } else {
        init.body = JSON.stringify({});
      }
      const res = await fetch(url, init);
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        setBannerError(payload.detail || payload.message || `Failed (${res.status})`);
        return;
      }
      setSuccessMsg(payload.detail || `${action} succeeded.`);
      await mutate();
    } catch (e) {
      setBannerError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <header className="mb-6 flex items-center justify-between gap-3">
        <Link
          href="/admin/support"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3 w-3" />
          Back to inbox
        </Link>
        <Button variant="ghost" size="sm" onClick={() => router.refresh()}>
          <RefreshCw className="mr-1 h-3 w-3" />
          Refresh
        </Button>
      </header>

      {bannerError && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="h-4 w-4" />
          {bannerError}
        </div>
      )}
      {successMsg && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-sm text-emerald-700">
          <CheckCircle2 className="h-4 w-4" />
          {successMsg}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-500/20 bg-red-500/5 px-3 py-2 text-sm text-red-700">
          {String(error.message || error)}
        </div>
      )}
      {(isLoading || !ticket) && !error && (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      )}

      {ticket && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_280px]">
          <main className="space-y-6">
            {/* Inbound */}
            <section className="rounded-md border">
              <header className="border-b px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Mail className="h-4 w-4 text-muted-foreground" />
                  <h2 className="font-medium">From {ticket.from_name || ticket.from_email}</h2>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {ticket.category}
                  </Badge>
                  {ticket.classifier_confidence !== null && (
                    <span className="text-xs text-muted-foreground">
                      Classifier:{" "}
                      <span className="font-mono">{(ticket.classifier_confidence * 100).toFixed(0)}%</span>
                    </span>
                  )}
                </div>
                <div className="mt-1 text-sm text-muted-foreground">
                  <span className="font-mono">{ticket.from_email}</span>
                </div>
                <div className="mt-2 text-sm">
                  <span className="font-medium">Subject:</span> {ticket.subject || "(no subject)"}
                </div>
              </header>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-4 py-3 text-sm font-mono">
                {ticket.body}
              </pre>
            </section>

            {/* AI draft */}
            <section className="rounded-md border">
              <header className="flex items-center justify-between border-b px-4 py-3">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-purple-500" />
                  <h2 className="font-medium">AI draft response</h2>
                  {ticket.ai_draft_confidence !== null && (
                    <Badge
                      variant="outline"
                      className={cn(
                        "font-mono text-[10px]",
                        ticket.ai_draft_confidence >= 7
                          ? "bg-emerald-500/10 text-emerald-700"
                          : ticket.ai_draft_confidence >= 4
                            ? "bg-amber-500/10 text-amber-700"
                            : "bg-red-500/10 text-red-700",
                      )}
                    >
                      {ticket.ai_draft_confidence.toFixed(1)}/10
                    </Badge>
                  )}
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy !== null || ticket.status === "sent"}
                  onClick={() => doAction("regenerate")}
                >
                  {busy === "regenerate" ? (
                    <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-1 h-3 w-3" />
                  )}
                  Regenerate
                </Button>
              </header>
              <div className="space-y-3 px-4 py-3">
                <div>
                  <Label htmlFor="ticket-subject">Subject</Label>
                  <Input
                    id="ticket-subject"
                    className="mt-1"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder="Re: …"
                    disabled={ticket.status === "sent"}
                  />
                </div>
                <div>
                  <Label htmlFor="ticket-body">Reply</Label>
                  <Textarea
                    id="ticket-body"
                    className="mt-1 min-h-[200px] font-sans"
                    value={editedBody}
                    onChange={(e) => setEditedBody(e.target.value)}
                    placeholder={
                      ticket.ai_draft_body
                        ? ""
                        : "No AI draft yet — write your reply or click Regenerate."
                    }
                    disabled={ticket.status === "sent"}
                  />
                </div>
              </div>
              <footer className="flex flex-wrap items-center justify-between gap-2 border-t bg-muted/30 px-4 py-3">
                <span className="text-xs text-muted-foreground">
                  Will send to: <span className="font-mono">{ticket.from_email}</span>
                </span>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy !== null || ticket.status === "sent" || ticket.status === "rejected"}
                    onClick={() => doAction("reject")}
                  >
                    {busy === "reject" ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : (
                      <XCircle className="mr-1 h-3 w-3" />
                    )}
                    Reject
                  </Button>
                  <Button
                    size="sm"
                    disabled={!canSend || busy !== null}
                    onClick={() => doAction("send")}
                  >
                    {busy === "send" ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : (
                      <Send className="mr-1 h-3 w-3" />
                    )}
                    Send via Gmail
                  </Button>
                </div>
              </footer>
            </section>
          </main>

          {/* Side panel */}
          <aside className="space-y-4">
            <section className="rounded-md border p-4">
              <h3 className="mb-2 text-sm font-medium">Status</h3>
              <Badge variant="outline" className="font-mono text-[10px]">
                {ticket.status}
              </Badge>
              {ticket.delivery_status && (
                <div className="mt-2 text-xs text-muted-foreground">
                  Delivery:{" "}
                  <span className="font-mono">
                    {String((ticket.delivery_status as any).status || "—")}
                  </span>
                </div>
              )}
              {ticket.agent_run_id && (
                <Link
                  href={`/admin/agent-runs?run=${ticket.agent_run_id}`}
                  className="mt-2 inline-block text-xs text-primary hover:underline"
                >
                  View agent run →
                </Link>
              )}
            </section>

            <section className="rounded-md border p-4">
              <h3 className="mb-2 text-sm font-medium">Sources used</h3>
              {!ticket.ai_draft_sources || ticket.ai_draft_sources.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No sources — the model didn't have relevant docs for this reply.
                </p>
              ) : (
                <ul className="space-y-2">
                  {ticket.ai_draft_sources.map((s, idx) => (
                    <li key={idx} className="text-xs">
                      <div className="flex items-start gap-1.5">
                        <FileText className="mt-0.5 h-3 w-3 text-muted-foreground" />
                        <div className="flex-1 min-w-0">
                          <div className="truncate font-medium">
                            {s.document_name || s.document_id || "Source"}
                          </div>
                          {s.excerpt && (
                            <div className="mt-1 line-clamp-3 text-muted-foreground">
                              {s.excerpt}
                            </div>
                          )}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}
