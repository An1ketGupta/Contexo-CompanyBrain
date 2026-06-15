"use client";

/**
 * Day 6 — Submit AI output for approval before it's executed.
 *
 * Shown in the message action row for any assistant turn that has a
 * server_id. The gating ("only paid plans") lives server-side in
 * `POST /approvals` — we still render the button on free plans so the
 * upsell error surfaces, rather than silently hiding the feature.
 *
 * The dialog asks for:
 *   1. Approver (any org member except the requester)
 *   2. Channel + destination params (gmail/slack — the two most common
 *      approval-gated flows). Notion / Google Docs follow the same shape
 *      and can be added later without changing the backend.
 *   3. Optional note (free-form context for the approver)
 *
 * Submission posts to /api/approvals → FastAPI mints a magic-link token,
 * fires the notification Inngest event, and returns the approval id. We
 * show the user a confirmation badge and stop showing the button for the
 * rest of the session.
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/hooks/use-user";

type Channel = "gmail" | "slack";

interface SubmitApprovalButtonProps {
  messageId: string;
  body: string;
}

interface Member {
  id: string;
  email: string | null;
  display_name: string | null;
  role: string;
}

interface SlackChannel {
  id: string;
  name: string;
  is_private?: boolean;
}

export function SubmitApprovalButton({ messageId, body }: SubmitApprovalButtonProps) {
  const [open, setOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  if (submitted) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-2 py-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
        <ShieldCheck className="h-3 w-3" />
        Awaiting approval
      </span>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center justify-center rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        title="Submit for approval"
        aria-label="Submit for approval"
      >
        <ShieldCheck className="h-3.5 w-3.5" />
      </button>

      {open && (
        <SubmitApprovalDialog
          messageId={messageId}
          defaultBody={body}
          onClose={() => setOpen(false)}
          onSubmitted={() => {
            setOpen(false);
            setSubmitted(true);
          }}
        />
      )}
    </>
  );
}

function SubmitApprovalDialog({
  messageId,
  defaultBody,
  onClose,
  onSubmitted,
}: {
  messageId: string;
  defaultBody: string;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const { user } = useCurrentUser();

  const [members, setMembers] = useState<Member[]>([]);
  const [approverId, setApproverId] = useState<string>("");
  const [channel, setChannel] = useState<Channel>("gmail");
  const [note, setNote] = useState("");

  // Gmail fields
  const [emailTo, setEmailTo] = useState("");
  const [emailSubject, setEmailSubject] = useState("");

  // Slack fields
  const [slackChannels, setSlackChannels] = useState<SlackChannel[]>([]);
  const [slackChannelId, setSlackChannelId] = useState("");

  const [bodyText, setBodyText] = useState(defaultBody);
  const [busy, setBusy] = useState(false);

  // Load org members (for the approver dropdown).
  useEffect(() => {
    let cancelled = false;
    fetch("/api/organizations/members", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((d) => {
        if (cancelled) return;
        const list = (d?.members ?? []) as Member[];
        setMembers(list.filter((m) => m.id !== user?.id));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  // Lazy-load slack channels only when the user picks Slack.
  useEffect(() => {
    if (channel !== "slack" || slackChannels.length > 0) return;
    fetch("/api/integrations/slack/channels", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)))
      .then((d) => setSlackChannels((d?.channels ?? []) as SlackChannel[]))
      .catch(() => {});
  }, [channel, slackChannels.length]);

  const canSubmit = useMemo(() => {
    if (!approverId || !bodyText.trim()) return false;
    if (channel === "gmail") return emailTo.trim().length > 0 && emailSubject.trim().length > 0;
    if (channel === "slack") return slackChannelId.trim().length > 0;
    return false;
  }, [approverId, bodyText, channel, emailTo, emailSubject, slackChannelId]);

  const selectedChannel = useMemo(
    () => slackChannels.find((c) => c.id === slackChannelId),
    [slackChannelId, slackChannels],
  );

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const execution_action =
        channel === "gmail"
          ? {
              channel: "gmail",
              params: {
                to: emailTo.trim(),
                subject: emailSubject.trim(),
                body: bodyText,
              },
            }
          : {
              channel: "slack",
              params: {
                channel_id: slackChannelId,
                channel_name: selectedChannel?.name ?? slackChannelId,
                text: bodyText,
              },
            };

      const res = await fetch("/api/approvals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: messageId,
          approver_id: approverId,
          execution_action,
          preview_text: [note.trim(), bodyText].filter(Boolean).join("\n\n").slice(0, 1800),
        }),
      });

      if (res.status === 402) {
        const body = await res.json().catch(() => ({}));
        toast.error(
          body?.detail ||
            "Submit for Approval is available on paid plans. Upgrade to enable it.",
        );
        return;
      }
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Failed (${res.status})`);
      }

      toast.success("Submitted for approval.");
      onSubmitted();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Submit for approval</DialogTitle>
          <DialogDescription>
            Pick an approver. They&apos;ll be notified by email and Slack and
            can approve or reject with one click. We execute the action only
            after they approve.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="approver">Approver</Label>
            <select
              id="approver"
              value={approverId}
              onChange={(e) => setApproverId(e.target.value)}
              className="mt-1 block w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              <option value="">Select a teammate…</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name || m.email || m.id}
                  {m.role === "admin" ? " (admin)" : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <Label>Action on approve</Label>
            <div className="mt-1 grid grid-cols-2 gap-2">
              {(["gmail", "slack"] as Channel[]).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setChannel(c)}
                  className={cn(
                    "rounded-md border px-3 py-2 text-left text-sm transition-colors",
                    channel === c
                      ? "border-foreground bg-muted"
                      : "border-input hover:bg-muted/40",
                  )}
                >
                  <div className="font-medium capitalize">
                    {c === "gmail" ? "Send via Gmail" : "Post to Slack"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {c === "gmail"
                      ? "Email a recipient on approval"
                      : "Post to a Slack channel on approval"}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {channel === "gmail" ? (
            <div className="space-y-2">
              <div>
                <Label htmlFor="email-to">To</Label>
                <Input
                  id="email-to"
                  type="email"
                  value={emailTo}
                  onChange={(e) => setEmailTo(e.target.value)}
                  placeholder="recipient@example.com"
                />
              </div>
              <div>
                <Label htmlFor="email-subject">Subject</Label>
                <Input
                  id="email-subject"
                  value={emailSubject}
                  onChange={(e) => setEmailSubject(e.target.value)}
                  placeholder="Subject line"
                />
              </div>
            </div>
          ) : (
            <div>
              <Label htmlFor="slack-channel">Channel</Label>
              <select
                id="slack-channel"
                value={slackChannelId}
                onChange={(e) => setSlackChannelId(e.target.value)}
                className="mt-1 block w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="">Select a channel…</option>
                {slackChannels.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.is_private ? "🔒 " : "#"}
                    {c.name}
                  </option>
                ))}
              </select>
              {slackChannels.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  Make sure Slack is connected in Settings → Integrations.
                </p>
              ) : null}
            </div>
          )}

          <div>
            <Label htmlFor="body">Draft</Label>
            <Textarea
              id="body"
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              rows={6}
              className="font-mono text-[12px]"
            />
          </div>

          <div>
            <Label htmlFor="note">Note to approver (optional)</Label>
            <Textarea
              id="note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="Anything they should know before approving…"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit || busy}>
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Submit for approval
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
