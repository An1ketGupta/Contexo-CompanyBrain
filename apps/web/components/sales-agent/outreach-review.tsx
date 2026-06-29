"use client";

import { useEffect, useState } from "react";
import { Loader2, Send, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  dealId: string;
  initialSubject: string;
  initialBody: string;
  initialLinkedIn: string | null;
  contactEmail: string | null;
  variant: "cold" | "followup" | "checkin";
  onMutated: () => void;
}

export function OutreachReview({
  dealId,
  initialSubject,
  initialBody,
  initialLinkedIn,
  contactEmail,
  variant,
  onMutated,
}: Props) {
  const [subject, setSubject] = useState(initialSubject ?? "");
  const [body, setBody] = useState(initialBody ?? "");
  const [linkedin, setLinkedIn] = useState(initialLinkedIn ?? "");
  const [saving, setSaving] = useState<null | "edit" | "approve" | "regen">(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSubject(initialSubject ?? "");
    setBody(initialBody ?? "");
    setLinkedIn(initialLinkedIn ?? "");
  }, [initialSubject, initialBody, initialLinkedIn]);

  async function saveEdits() {
    setSaving("edit");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/outreach`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject,
          email_body: body,
          linkedin_body: linkedin || null,
        }),
      });
      if (!res.ok) throw new Error(`Save failed: ${res.status}`);
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(null);
    }
  }

  async function approveAndSend() {
    if (!contactEmail) {
      setError("Add a contact email before sending.");
      return;
    }
    // Save first so the upstream sees the latest edits.
    await saveEdits();
    setSaving("approve");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/outreach/approve`, {
        method: "POST",
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b.detail || b.message || `Send failed: ${res.status}`);
      }
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Send failed");
    } finally {
      setSaving(null);
    }
  }

  async function regenerate() {
    setSaving("regen");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/outreach/regenerate`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Regenerate failed: ${res.status}`);
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Regenerate failed");
    } finally {
      setSaving(null);
    }
  }

  const variantLabel =
    variant === "cold" ? "Cold outreach" : variant === "followup" ? "Follow-up" : "Check-in";

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <header className="flex items-start justify-between">
        <div>
          <h3 className="text-lg font-semibold">{variantLabel} draft</h3>
          <p className="text-sm text-muted-foreground">
            Edit inline, regenerate, or approve and send via Gmail.
          </p>
        </div>
      </header>

      <div className="grid gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`subject-${dealId}`}>Subject</Label>
          <Input
            id={`subject-${dealId}`}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`body-${dealId}`}>Email body</Label>
          <Textarea
            id={`body-${dealId}`}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={12}
          />
        </div>
        {variant === "cold" ? (
          <div className="grid gap-1.5">
            <Label htmlFor={`li-${dealId}`}>LinkedIn note (optional)</Label>
            <Textarea
              id={`li-${dealId}`}
              value={linkedin}
              onChange={(e) => setLinkedIn(e.target.value)}
              rows={3}
            />
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
          {error}
        </div>
      ) : null}

      <div className="flex items-center justify-end gap-2">
        <Button variant="outline" onClick={regenerate} disabled={saving !== null}>
          {saving === "regen" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          Regenerate
        </Button>
        <Button variant="outline" onClick={saveEdits} disabled={saving !== null}>
          {saving === "edit" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Save edits
        </Button>
        <Button onClick={approveAndSend} disabled={saving !== null || !contactEmail}>
          {saving === "approve" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Send className="mr-2 h-4 w-4" />
          )}
          Approve & send
        </Button>
      </div>
      {!contactEmail ? (
        <p className="text-xs text-amber-600">
          No contact email on the deal — add one before approving.
        </p>
      ) : null}
    </div>
  );
}
