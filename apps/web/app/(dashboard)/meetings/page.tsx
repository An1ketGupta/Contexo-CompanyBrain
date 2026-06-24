"use client";

import Link from "next/link";
import useSWR from "swr";
import {
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  FileText,
  Loader2,
  Users,
} from "lucide-react";

import { formatDistanceToNow } from "@/lib/date";

interface MeetingSummary {
  id: string;
  source_document_id: string | null;
  source_document_name: string | null;
  derived_document_id: string | null;
  source_format: string;
  meeting_started_at: string | null;
  meeting_duration_seconds: number | null;
  summary: string | null;
  attendee_count: number;
  decision_count: number;
  action_item_count: number;
  slack_posted_at: string | null;
  created_at: string;
}

interface MeetingListResponse {
  summaries: MeetingSummary[];
  total: number;
  limit: number;
  offset: number;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const FORMAT_LABEL: Record<string, string> = {
  zoom_vtt: "Zoom",
  teams_json: "Teams",
  unknown: "Transcript",
};

function formatDuration(seconds: number | null): string | null {
  if (!seconds || seconds <= 0) return null;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

export default function MeetingsPage() {
  const { data, error, isLoading } = useSWR<MeetingListResponse>(
    "/api/meetings",
    fetcher,
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Meeting summaries</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Structured extractions from uploaded Zoom and Teams transcripts.
          Attendees, decisions, action items — all searchable through your
          chat.
        </p>
      </header>

      {isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : error ? (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load meeting summaries.
        </div>
      ) : !data?.summaries.length ? (
        <EmptyState />
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border bg-background">
          {data.summaries.map((m) => (
            <li key={m.id}>
              <Link
                href={`/meetings/${m.id}`}
                className="flex items-center gap-4 px-4 py-3 hover:bg-muted/40"
              >
                <CalendarDays className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium">
                      {m.source_document_name ?? "Untitled meeting"}
                    </p>
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {FORMAT_LABEL[m.source_format] ?? m.source_format}
                    </span>
                    {formatDuration(m.meeting_duration_seconds) ? (
                      <span className="text-xs text-muted-foreground">
                        {formatDuration(m.meeting_duration_seconds)}
                      </span>
                    ) : null}
                  </div>
                  {m.summary ? (
                    <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                      {m.summary}
                    </p>
                  ) : null}
                  <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      {m.attendee_count} attendees
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <CheckCircle2 className="h-3 w-3" />
                      {m.decision_count} decisions
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <FileText className="h-3 w-3" />
                      {m.action_item_count} action items
                    </span>
                    <span>
                      {formatDistanceToNow(
                        m.meeting_started_at || m.created_at,
                      )}
                    </span>
                  </div>
                </div>
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-background p-8 text-center">
      <CalendarDays className="mx-auto h-8 w-8 text-muted-foreground" />
      <p className="mt-3 text-sm font-medium">No meeting summaries yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Upload a Zoom <code>.vtt</code> or Teams transcript JSON to your{" "}
        <Link href="/documents" className="underline">
          documents
        </Link>{" "}
        and Company Brain will extract attendees, decisions, and action items
        automatically.
      </p>
    </div>
  );
}
