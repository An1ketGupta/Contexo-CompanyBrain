"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

const MEETING_TYPES = [
  "Investor Meeting",
  "Client Pitch",
  "Team All-Hands",
  "One-on-One",
  "Board Meeting",
  "Sales Call",
  "Performance Review",
  "Project Kickoff",
  "Other",
] as const;

type MeetingType = (typeof MEETING_TYPES)[number];

export function MeetingPrepPanel() {
  const router = useRouter();
  const [meetingType, setMeetingType] = useState<MeetingType | "">("");
  const [attendees, setAttendees] = useState("");
  const [topics, setTopics] = useState("");
  const [date, setDate] = useState("");
  const [additionalContext, setAdditionalContext] = useState("");
  const [loading, setLoading] = useState(false);

  const canSubmit =
    !loading &&
    meetingType !== "" &&
    attendees.trim().length > 0 &&
    topics.trim().length > 0;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setLoading(true);
    try {
      const res = await fetch("/api/chat/meeting-prep", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_type: meetingType,
          attendees: attendees.trim(),
          topics: topics.trim(),
          date: date || null,
          additional_context: additionalContext.trim() || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      const data = (await res.json()) as {
        conversation_id: string;
        prompt: string;
      };
      // Stash the prompt for the conversation page to auto-send. Keyed by id
      // so a stale prefill from another nav never fires here.
      if (typeof window !== "undefined") {
        window.sessionStorage.setItem(
          `chat-prefill:${data.conversation_id}`,
          data.prompt,
        );
      }
      router.push(`/chat/${data.conversation_id}`);
    } catch (err) {
      toast.error((err as Error).message || "Couldn't start the brief.");
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl space-y-6">
      <div className="space-y-1.5">
        <Label>Meeting type</Label>
        <div className="flex flex-wrap gap-2">
          {MEETING_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => setMeetingType(type)}
              className={cn(
                "rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors",
                meetingType === type
                  ? "border-transparent bg-brand-tint font-semibold text-brand"
                  : "border-border text-foreground hover:border-input hover:bg-muted",
              )}
            >
              {type}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="attendees">
          Who&apos;s attending?{" "}
          <span className="font-normal text-muted-foreground">
            (names, roles, or company)
          </span>
        </Label>
        <Input
          id="attendees"
          value={attendees}
          onChange={(e) => setAttendees(e.target.value)}
          placeholder="e.g. Sarah Chen (CTO at Acme), our VP Sales"
          maxLength={600}
          disabled={loading}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="topics">Key topics to cover</Label>
        <Textarea
          id="topics"
          value={topics}
          onChange={(e) => setTopics(e.target.value)}
          placeholder="e.g. Q3 roadmap, pricing discussion, integration timeline"
          rows={3}
          maxLength={2000}
          disabled={loading}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="date">Meeting date</Label>
        <Input
          id="date"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          disabled={loading}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="extra">
          Additional context{" "}
          <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Textarea
          id="extra"
          value={additionalContext}
          onChange={(e) => setAdditionalContext(e.target.value)}
          placeholder="Specific concerns, recent history, anything the AI should know…"
          rows={2}
          maxLength={2000}
          disabled={loading}
        />
      </div>

      <Button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full"
        size="lg"
      >
        {loading ? (
          <>
            <Loader2 className="animate-spin" />
            Generating your brief…
          </>
        ) : (
          <>
            <Sparkles className="h-4 w-4" />
            Generate Prep brief
          </>
        )}
      </Button>
    </div>
  );
}
