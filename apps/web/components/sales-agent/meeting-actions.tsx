"use client";

import { useState } from "react";
import { Loader2, CalendarPlus, FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  dealId: string;
  hasPrepReady: boolean;
  onMutated: () => void;
}

export function MeetingActions({ dealId, hasPrepReady, onMutated }: Props) {
  const [meetingAt, setMeetingAt] = useState("");
  const [notes, setNotes] = useState("");
  const [transcript, setTranscript] = useState("");
  const [saving, setSaving] = useState<null | "book" | "transcript">(null);
  const [error, setError] = useState<string | null>(null);

  async function bookMeeting() {
    if (!meetingAt) {
      setError("Pick a date/time.");
      return;
    }
    setSaving("book");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/meeting-booked`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_at: new Date(meetingAt).toISOString(),
          notes: notes || null,
        }),
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      setMeetingAt("");
      setNotes("");
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(null);
    }
  }

  async function submitTranscript() {
    if (transcript.trim().length < 20) {
      setError("Transcript looks too short.");
      return;
    }
    setSaving("transcript");
    setError(null);
    try {
      const res = await fetch(`/api/sales/deals/runs/${dealId}/call-transcript`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript }),
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      setTranscript("");
      onMutated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="space-y-6 rounded-lg border p-4">
      <div className="grid gap-3">
        <h3 className="text-lg font-semibold">Meeting</h3>
        <div className="grid gap-1.5">
          <Label htmlFor={`meeting-at-${dealId}`}>Booked time</Label>
          <Input
            id={`meeting-at-${dealId}`}
            type="datetime-local"
            value={meetingAt}
            onChange={(e) => setMeetingAt(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`meeting-notes-${dealId}`}>Notes (optional)</Label>
          <Textarea
            id={`meeting-notes-${dealId}`}
            rows={2}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <Button onClick={bookMeeting} disabled={saving !== null}>
          {saving === "book" ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <CalendarPlus className="mr-2 h-4 w-4" />
          )}
          Mark meeting booked
        </Button>
      </div>

      {hasPrepReady ? (
        <div className="grid gap-3 border-t pt-4">
          <h3 className="text-lg font-semibold">After the call</h3>
          <div className="grid gap-1.5">
            <Label htmlFor={`transcript-${dealId}`}>Paste transcript or notes</Label>
            <Textarea
              id={`transcript-${dealId}`}
              rows={8}
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder="Raw transcript, Otter export, or your own notes. The agent will extract BANT, objections, next steps."
            />
          </div>
          <Button onClick={submitTranscript} disabled={saving !== null}>
            {saving === "transcript" ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <FileText className="mr-2 h-4 w-4" />
            )}
            Submit & extract summary
          </Button>
        </div>
      ) : null}

      {error ? (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-600">
          {error}
        </div>
      ) : null}
    </div>
  );
}
