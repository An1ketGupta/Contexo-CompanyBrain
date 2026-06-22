"use client";

import { useMemo, useState } from "react";
import { AlertCircle, Eye, Loader2, Paperclip, Pencil, Send } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Markdown } from "./markdown";

interface SendGmailDialogProps {
  messageId: string;
  defaultBody: string;
  senderEmail: string | null;
  onClose: () => void;
  onSent: (recipient: string) => void;
}

/**
 * Extract a subject line if the assistant followed our "Subject: …" convention.
 * Returns { subject, body } where the subject line (if any) has been peeled
 * off the body so we don't duplicate it in the rendered email.
 */
function splitSubjectAndBody(raw: string): { subject: string; body: string } {
  const trimmed = raw.trim();
  const firstLineBreak = trimmed.indexOf("\n");
  const firstLine = firstLineBreak === -1 ? trimmed : trimmed.slice(0, firstLineBreak);
  const subjectMatch = firstLine.match(/^\s*\*?\*?\s*Subject\s*:\s*(.+?)\s*\*?\*?\s*$/i);
  if (subjectMatch) {
    return {
      subject: subjectMatch[1].trim(),
      body: trimmed.slice(firstLineBreak + 1).trimStart(),
    };
  }
  return { subject: "", body: trimmed };
}

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function SendGmailDialog({
  messageId,
  defaultBody,
  senderEmail,
  onClose,
  onSent,
}: SendGmailDialogProps) {
  const initial = useMemo(() => splitSubjectAndBody(defaultBody), [defaultBody]);
  const [to, setTo] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState(initial.subject);
  const [body, setBody] = useState(initial.body);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [includeSources, setIncludeSources] = useState(true);
  const [bodyMode, setBodyMode] = useState<"edit" | "preview">("preview");

  const canSend = to.trim().length > 0 && EMAIL_REGEX.test(to.trim()) && subject.trim().length > 0;

  const handleSend = async () => {
    if (!canSend || submitting) return;
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await fetch("/api/integrations/gmail/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: messageId,
          to: to.trim(),
          subject: subject.trim(),
          body,
          cc: cc.trim() || null,
          include_sources: includeSources,
          acknowledged_warnings: true,
        }),
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        const detail = payload.detail ?? "send_failed";
        if (detail === "gmail_send_scope_missing") {
          setErrorMessage("Your Gmail connection is missing send permission. Reconnect from Settings → Integrations.");
        } else if (detail === "gmail_not_connected") {
          setErrorMessage("Gmail is not connected for your account.");
        } else if (detail === "message_already_sent") {
          setErrorMessage("This message has already been sent.");
        } else if (detail === "message_not_found") {
          setErrorMessage("Could not find the original message. Try refreshing.");
        } else if (detail === "confidence_below_block") {
          setErrorMessage(
            "This answer's confidence is below your workspace's publish threshold. An admin can adjust this in Admin → Confidence.",
          );
        } else if (detail === "outbound_rate_limited") {
          const retry = res.headers.get("Retry-After");
          setErrorMessage(
            retry
              ? `Email send rate limit hit. Try again in ${Math.ceil(Number(retry) / 60)} min.`
              : "Email send rate limit hit. Try again later.",
          );
        } else if (detail === "competitor_match_unacknowledged") {
          setErrorMessage("Competitor mentions require explicit acknowledgement — please retry.");
        } else {
          setErrorMessage("Couldn't queue the email. Please try again.");
        }
        return;
      }
      onSent(to.trim());
    } catch {
      setErrorMessage("Network error — please check your connection and retry.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Send via Gmail</DialogTitle>
          {senderEmail && (
            <DialogDescription>
              Sending as <span className="font-medium text-foreground">{senderEmail}</span>
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="gmail-to">To</Label>
            <Input
              id="gmail-to"
              type="email"
              placeholder="recipient@company.com"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="gmail-cc">CC <span className="text-muted-foreground">(optional)</span></Label>
            <Input
              id="gmail-cc"
              type="email"
              placeholder="cc@company.com"
              value={cc}
              onChange={(e) => setCc(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="gmail-subject">Subject</Label>
            <Input
              id="gmail-subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Add a subject"
            />
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor="gmail-body">Body</Label>
              <div className="flex items-center rounded-md border border-border overflow-hidden">
                <button
                  type="button"
                  onClick={() => setBodyMode("preview")}
                  className={`flex items-center gap-1 px-2 py-0.5 text-xs transition-colors ${
                    bodyMode === "preview"
                      ? "bg-muted text-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Eye className="h-3 w-3" />
                  Preview
                </button>
                <button
                  type="button"
                  onClick={() => setBodyMode("edit")}
                  className={`flex items-center gap-1 px-2 py-0.5 text-xs transition-colors ${
                    bodyMode === "edit"
                      ? "bg-muted text-foreground font-medium"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Pencil className="h-3 w-3" />
                  Edit
                </button>
              </div>
            </div>
            {bodyMode === "preview" ? (
              <div className="min-h-[160px] max-h-[280px] overflow-y-auto rounded-md border border-border bg-background px-3 py-2">
                <Markdown>{body}</Markdown>
              </div>
            ) : (
              <Textarea
                id="gmail-body"
                rows={10}
                value={body}
                onChange={(e) => setBody(e.target.value)}
                className="font-mono text-xs"
              />
            )}
          </div>

          <label
            htmlFor="gmail-include-sources"
            className="flex items-start gap-2 rounded-md border border-border/60 bg-muted/30 p-2 text-xs cursor-pointer hover:bg-muted/50 transition-colors"
          >
            <Checkbox
              id="gmail-include-sources"
              checked={includeSources}
              onCheckedChange={(v) => setIncludeSources(v === true)}
              className="mt-0.5"
            />
            <span className="flex flex-col gap-0.5">
              <span className="flex items-center gap-1.5 font-medium text-foreground">
                <Paperclip className="h-3 w-3" />
                Attach source material
              </span>
              <span className="text-muted-foreground">
                Sends a <code className="font-mono text-[10px]">Sources_used.txt</code> file
                with the full retrieved context (the chunks NirnayaIQ used to draft this email).
              </span>
            </span>
          </label>

          {errorMessage && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSend} disabled={!canSend || submitting}>
            {submitting ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="mr-2 h-3.5 w-3.5" />
            )}
            Send
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
