"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  FileText,
  Loader2,
  RefreshCw,
  MessageSquare,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatDistanceToNow } from "@/lib/date";
import { useCurrentUser } from "@/hooks/use-user";

interface Attendee {
  name: string;
  role?: string;
  utterance_count?: number;
}

interface ActionItem {
  owner: string;
  task: string;
  due: string;
}

interface MeetingSummaryDetail {
  id: string;
  org_id: string;
  source_document_id: string | null;
  derived_document_id: string | null;
  source_document_name: string | null;
  derived_document_name: string | null;
  source_format: string;
  meeting_started_at: string | null;
  meeting_duration_seconds: number | null;
  summary: string | null;
  attendees: Attendee[];
  decisions: string[];
  action_items: ActionItem[];
  slack_channel_id: string | null;
  slack_posted_at: string | null;
  agent_run_id: string | null;
  created_at: string;
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function MeetingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const { data, error, isLoading, mutate } = useSWR<MeetingSummaryDetail>(
    id ? `/api/meetings/${id}` : null,
    fetcher,
  );
  const [reprocessing, setReprocessing] = useState(false);

  const handleReprocess = async () => {
    if (!id) return;
    if (
      !confirm(
        "Reprocess this meeting? The existing structured summary will be deleted and the agent will re-run.",
      )
    )
      return;
    setReprocessing(true);
    try {
      const res = await fetch(`/api/meetings/${id}/reprocess`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      toast.success("Reprocess queued — refresh in a minute.");
      // Optimistically mutate; the row will reappear once the agent finishes.
      mutate();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setReprocessing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="p-6 text-sm text-destructive">
        Failed to load this meeting summary.
      </div>
    );
  }

  const durationMinutes = data.meeting_duration_seconds
    ? Math.round(data.meeting_duration_seconds / 60)
    : null;

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <Link
        href="/meetings"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to meetings
      </Link>

      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <CalendarDays className="h-4 w-4 text-muted-foreground" />
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {data.source_document_name ?? "Untitled meeting"}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>
              {data.source_format === "zoom_vtt" ? "Zoom" : "Teams"} transcript
            </span>
            {durationMinutes ? <span>{durationMinutes} min</span> : null}
            <span>
              {formatDistanceToNow(
                data.meeting_started_at || data.created_at,
              )}
            </span>
            {data.slack_posted_at ? (
              <span className="inline-flex items-center gap-1">
                <MessageSquare className="h-3 w-3" /> action items posted to Slack
              </span>
            ) : null}
          </div>
        </div>
        {isAdmin ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={handleReprocess}
            disabled={reprocessing}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${reprocessing ? "animate-spin" : ""}`}
            />
            Reprocess
          </Button>
        ) : null}
      </header>

      {data.summary ? (
        <section className="rounded-lg border border-border bg-background p-4">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Summary
          </p>
          <p className="mt-2 text-sm leading-relaxed">{data.summary}</p>
        </section>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-lg border border-border bg-background p-4">
          <header className="flex items-center gap-2 text-sm font-medium">
            <Users className="h-4 w-4" /> Attendees ({data.attendees.length})
          </header>
          <ul className="mt-3 space-y-1.5 text-sm">
            {data.attendees.length ? (
              data.attendees.map((a) => (
                <li key={a.name} className="flex items-center justify-between">
                  <span>{a.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {a.role && a.role !== "unknown" ? a.role : "—"}
                    {a.utterance_count ? ` · ${a.utterance_count} turns` : ""}
                  </span>
                </li>
              ))
            ) : (
              <li className="text-xs text-muted-foreground">
                No attendees detected.
              </li>
            )}
          </ul>
        </section>

        <section className="rounded-lg border border-border bg-background p-4">
          <header className="flex items-center gap-2 text-sm font-medium">
            <CheckCircle2 className="h-4 w-4" /> Decisions (
            {data.decisions.length})
          </header>
          <ul className="mt-3 space-y-1.5 text-sm">
            {data.decisions.length ? (
              data.decisions.map((d, i) => (
                <li key={i} className="leading-snug">
                  · {d}
                </li>
              ))
            ) : (
              <li className="text-xs text-muted-foreground">
                No explicit decisions recorded.
              </li>
            )}
          </ul>
        </section>
      </div>

      <section className="rounded-lg border border-border bg-background p-4">
        <header className="flex items-center gap-2 text-sm font-medium">
          <FileText className="h-4 w-4" /> Action items (
          {data.action_items.length})
        </header>
        <ul className="mt-3 space-y-2 text-sm">
          {data.action_items.length ? (
            data.action_items.map((item, i) => (
              <li
                key={i}
                className="rounded-md border border-border/50 px-3 py-2"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-medium">
                    {item.owner === "Unassigned" ? (
                      <span className="text-muted-foreground">Unassigned</span>
                    ) : (
                      item.owner
                    )}
                  </p>
                  {item.due && item.due.toLowerCase() !== "unspecified" ? (
                    <span className="text-xs text-muted-foreground">
                      due {item.due}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-sm leading-snug">{item.task}</p>
              </li>
            ))
          ) : (
            <li className="text-xs text-muted-foreground">
              No action items extracted.
            </li>
          )}
        </ul>
      </section>

      {data.derived_document_id ? (
        <div className="rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
          The structured body of this meeting is also indexed as a searchable
          document.{" "}
          <Link
            href={`/documents?focus=${data.derived_document_id}`}
            className="underline"
          >
            Open "{data.derived_document_name ?? "Meeting summary"}"
          </Link>
          .
        </div>
      ) : null}
    </div>
  );
}
