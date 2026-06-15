"use client";

import { useMemo, useState } from "react";
import { AlertCircle, Loader2, Send } from "lucide-react";
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
            <Label htmlFor="gmail-body">Body</Label>
            <Textarea
              id="gmail-body"
              rows={10}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              className="font-mono text-xs"
            />
          </div>

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
