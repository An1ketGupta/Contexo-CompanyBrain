"use client";

import { useState } from "react";
import { Calendar, ChevronDown, ChevronUp, Sparkles, Users } from "lucide-react";

// Mirrors apps/api/app/routers/meeting_prep.py::_build_query. If that prompt
// shape changes, this parser falls through to null and the raw text bubble
// renders instead — never a broken card.
const MEETING_PREP_PREFIX =
  "Prepare a structured meeting brief grounded in our knowledge base.";

export interface ParsedMeetingPrep {
  meetingType: string;
  date: string | null;
  attendees: string;
  topics: string;
  additionalContext: string | null;
}

export function parseMeetingPrepPrompt(content: string): ParsedMeetingPrep | null {
  if (!content.startsWith(MEETING_PREP_PREFIX)) return null;
  const typeMatch = content.match(/\nMeeting type:\s*(.+)/);
  const dateMatch = content.match(/\nDate:\s*(.+)/);
  // Attendees and topics come from free-text inputs (topics is a <Textarea>,
  // so it can contain newlines) — capture non-greedily up to the next known
  // section boundary rather than assuming a single line.
  const attendeesMatch = content.match(/\nAttendees:\s*([\s\S]*?)\nKey topics:/);
  const topicsMatch = content.match(
    /\nKey topics:\s*([\s\S]*?)\n\n(?:Additional context:|Search the knowledge base)/,
  );
  const extraMatch = content.match(
    /\nAdditional context:\s*([\s\S]*?)\n\nSearch the knowledge base/,
  );
  if (!typeMatch || !attendeesMatch || !topicsMatch) return null;
  return {
    meetingType: typeMatch[1].trim(),
    date: dateMatch ? dateMatch[1].trim() : null,
    attendees: attendeesMatch[1].trim(),
    topics: topicsMatch[1].trim(),
    additionalContext: extraMatch ? extraMatch[1].trim() : null,
  };
}

export function MeetingPrepRequestCard({
  parsed,
  raw,
}: {
  parsed: ParsedMeetingPrep;
  raw: string;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-primary-foreground/80">
        <Sparkles className="h-3.5 w-3.5" />
        Meeting prep — {parsed.meetingType}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-primary-foreground/90">
        {parsed.date && (
          <span className="inline-flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            {parsed.date}
          </span>
        )}
        <span className="inline-flex items-center gap-1">
          <Users className="h-3 w-3" />
          {parsed.attendees}
        </span>
      </div>

      <div className="whitespace-pre-wrap break-words text-sm leading-6">
        <span className="font-medium text-primary-foreground/70">Topics: </span>
        {parsed.topics}
      </div>

      {parsed.additionalContext && (
        <div className="whitespace-pre-wrap break-words text-sm leading-6">
          <span className="font-medium text-primary-foreground/70">Context: </span>
          {parsed.additionalContext}
        </div>
      )}

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="inline-flex items-center gap-0.5 text-[11px] font-medium text-primary-foreground/70 underline-offset-2 hover:underline"
      >
        {expanded ? (
          <>
            Hide full prompt <ChevronUp className="h-3 w-3" />
          </>
        ) : (
          <>
            View full prompt <ChevronDown className="h-3 w-3" />
          </>
        )}
      </button>

      {expanded && (
        <div className="whitespace-pre-wrap break-words rounded-lg bg-black/10 p-2 text-xs text-primary-foreground/80">
          {raw}
        </div>
      )}
    </div>
  );
}
