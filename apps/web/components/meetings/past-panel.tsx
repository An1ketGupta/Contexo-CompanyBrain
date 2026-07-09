"use client";

import { useRef } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import {
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  FileText,
  Loader2,
  Upload,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useUploads } from "@/components/documents/upload-context";
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

export function PastMeetingsPanel() {
  const { data, error, isLoading } = useSWR<MeetingListResponse>(
    "/api/meetings",
    fetcher,
  );

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Structured extractions from uploaded meeting transcripts.
        </p>
        <TranscriptUploadButton />
      </div>

      {isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : error ? (
        <div className="rounded-xl border border-destructive/50 bg-destructive/5 p-4 text-sm text-destructive">
          Failed to load meeting summaries.
        </div>
      ) : !data?.summaries.length ? (
        <EmptyState />
      ) : (
        <ul className="divide-y divide-border rounded-xl border border-border bg-card">
          {data.summaries.map((m) => (
            <li key={m.id}>
              <Link
                href={`/meetings/${m.id}`}
                className="flex items-center gap-4 px-4 py-3 transition-colors hover:bg-muted/50"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-tint text-brand">
                  <CalendarDays className="h-4 w-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium">
                      {m.source_document_name ?? "Untitled meeting"}
                    </p>
                    <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-secondary-foreground">
                      {FORMAT_LABEL[m.source_format] ?? m.source_format}
                    </span>
                    {formatDuration(m.meeting_duration_seconds) ? (
                      <span className="text-xs text-muted-foreground">
                        {formatDuration(m.meeting_duration_seconds)}
                      </span>
                    ) : null}
                  </div>
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
    <div className="rounded-xl border border-dashed border-border bg-background p-10 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
        <CalendarDays className="h-5 w-5" />
      </div>
      <p className="mt-3 text-sm font-medium">No meeting summaries yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Upload a Zoom <code>.vtt</code> or Teams transcript JSON and Nirnaya IQ
        will extract attendees, decisions, and action items automatically.
      </p>
      <div className="mt-4 flex justify-center">
        <TranscriptUploadButton />
      </div>
    </div>
  );
}

// Transcript-only entry point into the shared document upload queue. Reuses the
// global uploader (progress widget + retry live in the dashboard layout), but
// scopes the picker to the two formats the MeetingNotesAgent understands. Once
// ingested, the backend fans out `meeting/transcript-uploaded` and the summary
// shows up in this list after the agent finishes.
function TranscriptUploadButton() {
  const { enqueue, startAll } = useUploads();
  const inputRef = useRef<HTMLInputElement>(null);

  const onPick = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const { added, rejected } = enqueue(files);
    if (added > 0) {
      startAll();
      toast.success(
        added === 1
          ? "Transcript uploading — extracting attendees, decisions, and action items. This can take a minute."
          : `${added} transcripts uploading — summaries will appear once processing finishes.`,
      );
    } else if (rejected > 0) {
      toast.error("Upload a Zoom .vtt or Teams transcript .json file.");
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".vtt,.json"
        className="hidden"
        onChange={(e) => {
          onPick(e.target.files);
          if (inputRef.current) inputRef.current.value = "";
        }}
      />
      <Button
        variant="outline"
        size="sm"
        className="shrink-0"
        onClick={() => inputRef.current?.click()}
      >
        <Upload className="h-4 w-4" />
        Upload transcript
      </Button>
    </>
  );
}
