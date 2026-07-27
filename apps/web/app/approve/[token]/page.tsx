"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import {
  CheckCircle2,
  Loader2,
  Mail,
  MessageSquare,
  Newspaper,
  FileText as FileTextIcon,
  XCircle,
} from "lucide-react";

import { Logo } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type Action = "approved" | "rejected";

interface LookupResponse {
  approval_id: string;
  status: string;
  preview_text: string | null;
  execution_action: {
    channel: string;
    destination: string | null;
    title: string | null;
  };
  requested_by_name: string | null;
  approver_name: string | null;
  org_name: string | null;
  created_at: string;
  token_expires_at: string;
}

const CHANNEL_META: Record<string, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  gmail: { label: "Send via Gmail", icon: Mail },
  slack: { label: "Post to Slack", icon: MessageSquare },
  notion: { label: "Create Notion page", icon: Newspaper },
  gdocs: { label: "Create Google Doc", icon: FileTextIcon },
};

export default function MagicLinkApprovalPage() {
  const params = useParams<{ token: string }>();
  const searchParams = useSearchParams();
  const token = params?.token ?? "";
  // The email magic-link sends `?decision=approve` or `?decision=reject`
  // (short forms — see `approval_functions.py`) to pre-highlight which
  // button the recipient came in to click. Keep this distinct from `Action`
  // (`"approved" | "rejected"`) which is the API-facing past-tense value.
  const decisionParam = searchParams?.get("decision");
  const initialDecision: "approve" | "reject" | null =
    decisionParam === "approve" || decisionParam === "reject"
      ? decisionParam
      : null;

  const [data, setData] = useState<LookupResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  const [resolved, setResolved] = useState<Action | null>(null);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await fetch(`/api/approvals/by-token?token=${encodeURIComponent(token)}`);
      if (res.status === 404) {
        setLoadError("This approval link is invalid or no longer exists.");
        return;
      }
      if (res.status === 410) {
        setLoadError("This approval link has expired or has already been resolved.");
        return;
      }
      if (!res.ok) {
        setLoadError("Couldn't load this approval. Try the link again.");
        return;
      }
      const body = (await res.json()) as LookupResponse;
      setData(body);
    } catch {
      setLoadError("Couldn't reach the server. Try again in a moment.");
    }
  }, [token]);

  useEffect(() => {
    if (token) load();
  }, [token, load]);

  async function resolve(action: Action) {
    if (!data) return;
    setBusy(action);
    try {
      const res = await fetch("/api/approvals/resolve-by-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, action, note: note.trim() || null }),
      });
      if (res.status === 410) {
        setLoadError("This approval has already been resolved.");
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setLoadError(body?.detail || "Couldn't resolve. Try again.");
        return;
      }
      setResolved(action);
    } finally {
      setBusy(null);
    }
  }

  const channelMeta = data
    ? (CHANNEL_META[data.execution_action.channel] ?? {
        label: data.execution_action.channel,
        icon: FileTextIcon,
      })
    : null;

  return (
    <div className="min-h-screen bg-muted/30 px-4 py-12">
      <div className="mx-auto max-w-xl rounded-lg border bg-card p-8 shadow-sm">
        <div className="mb-6 border-b pb-4">
          <Logo height={15} />
          <h1 className="mt-1 text-xl font-semibold tracking-tight">
            {resolved
              ? resolved === "approved"
                ? "Approved"
                : "Rejected"
              : "Approve or reject"}
          </h1>
        </div>

        {loadError ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            {loadError}
          </div>
        ) : !data ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading…
          </div>
        ) : resolved ? (
          <ResolvedState
            action={resolved}
            requesterName={data.requested_by_name}
          />
        ) : (
          <>
            <div className="space-y-3">
              <div className="text-sm text-muted-foreground">
                <span className="font-medium text-foreground">
                  {data.requested_by_name ?? "A teammate"}
                </span>{" "}
                {data.org_name ? `at ${data.org_name} ` : ""}
                is asking you to approve:
              </div>

              <div className="flex items-center gap-3 rounded-md border bg-muted/40 p-3 text-sm">
                {channelMeta ? (
                  <channelMeta.icon className="h-4 w-4 text-muted-foreground" />
                ) : null}
                <div>
                  <div className="font-medium">{channelMeta?.label}</div>
                  {data.execution_action.destination ? (
                    <div className="text-xs text-muted-foreground">
                      → {data.execution_action.destination}
                    </div>
                  ) : null}
                </div>
              </div>

              {data.preview_text ? (
                <div className="rounded-md border bg-muted/40 p-3 text-sm">
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Draft preview
                  </div>
                  <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap font-sans text-[13px] leading-relaxed">
                    {data.preview_text}
                  </pre>
                </div>
              ) : null}

              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">
                  Note (optional)
                </label>
                <Textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Add a note for the requester…"
                  rows={3}
                />
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-2">
              <Button
                variant="outline"
                disabled={Boolean(busy)}
                onClick={() => resolve("rejected")}
                className={cn(initialDecision === "reject" && "ring-2 ring-foreground/20")}
              >
                {busy === "rejected" ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <XCircle className="mr-2 h-4 w-4" />
                )}
                Reject
              </Button>
              <Button
                disabled={Boolean(busy)}
                onClick={() => resolve("approved")}
                className={cn(initialDecision === "approve" && "ring-2 ring-foreground/20")}
              >
                {busy === "approved" ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                )}
                Approve
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ResolvedState({
  action,
  requesterName,
}: {
  action: Action;
  requesterName: string | null;
}) {
  const approved = action === "approved";
  return (
    <div className="flex flex-col items-center py-4 text-center">
      {approved ? (
        <CheckCircle2 className="h-10 w-10 text-emerald-500" />
      ) : (
        <XCircle className="h-10 w-10 text-red-500" />
      )}
      <h2 className="mt-3 text-base font-medium">
        {approved
          ? "Request approved — execution is now queued."
          : "Request rejected."}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        {requesterName ?? "The requester"} has been notified.
      </p>
    </div>
  );
}
