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
  ClipboardList,
  FileText,
  Loader2,
  RefreshCw,
  MessageSquare,
  Sparkles,
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

const FORMAT_LABEL: Record<string, string> = {
  zoom_vtt: "Zoom",
  teams_json: "Teams",
  unknown: "Transcript",
};

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return parts
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

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
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading meeting…
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="mx-auto max-w-3xl p-6 md:p-8">
        <div className="rounded-xl border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive-ink">
          Failed to load this meeting summary.
        </div>
      </div>
    );
  }

  const durationMinutes = data.meeting_duration_seconds
    ? Math.round(data.meeting_duration_seconds / 60)
    : null;
  const formatLabel = FORMAT_LABEL[data.source_format] ?? "Transcript";

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <Link
        href="/meetings/transcripts"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to transcripts
      </Link>

      <header className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-brand">
            <CalendarDays className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-extrabold tracking-tight">
              {data.source_document_name ?? "Untitled meeting"}
            </h1>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-secondary-foreground">
                {formatLabel} transcript
              </span>
              {durationMinutes ? (
                <span className="text-xs text-muted-foreground">
                  {durationMinutes} min
                </span>
              ) : null}
              <span className="text-xs text-muted-foreground">
                {formatDistanceToNow(
                  data.meeting_started_at || data.created_at,
                )}
              </span>
              {data.slack_posted_at ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-success-tint px-2 py-0.5 text-[11px] font-medium text-success-ink">
                  <MessageSquare className="h-3 w-3" /> Posted to Slack
                </span>
              ) : null}
            </div>
          </div>
        </div>
        {isAdmin ? (
          <Button
            size="sm"
            variant="outline"
            className="shrink-0"
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
        <section className="rounded-xl border border-border bg-card p-5">
          <SectionHeader
            icon={Sparkles}
            tint="bg-brand-tint text-brand"
            label="Summary"
          />
          <p className="mt-3 text-sm leading-relaxed text-body">
            {data.summary}
          </p>
        </section>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-xl border border-border bg-card p-5">
          <SectionHeader
            icon={Users}
            tint="bg-violet-tint text-violet"
            label="Attendees"
            count={data.attendees.length}
          />
          <ul className="mt-4 space-y-3">
            {data.attendees.length ? (
              data.attendees.map((a) => {
                const meta = [
                  a.role && a.role !== "unknown" ? a.role : null,
                  a.utterance_count ? `${a.utterance_count} turns` : null,
                ]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <li key={a.name} className="flex items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary font-mono text-[11px] font-bold text-body">
                      {initials(a.name)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{a.name}</p>
                      {meta ? (
                        <p className="truncate text-xs text-muted-foreground">
                          {meta}
                        </p>
                      ) : null}
                    </div>
                  </li>
                );
              })
            ) : (
              <li className="text-xs text-muted-foreground">
                No attendees detected.
              </li>
            )}
          </ul>
        </section>

        <section className="rounded-xl border border-border bg-card p-5">
          <SectionHeader
            icon={CheckCircle2}
            tint="bg-success-tint text-success-ink"
            label="Decisions"
            count={data.decisions.length}
          />
          <ul className="mt-4 space-y-2.5">
            {data.decisions.length ? (
              data.decisions.map((d, i) => (
                <li
                  key={i}
                  className="flex gap-2.5 text-sm leading-snug text-body"
                >
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-success" />
                  <span>{d}</span>
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

      <section className="rounded-xl border border-border bg-card p-5">
        <SectionHeader
          icon={ClipboardList}
          tint="bg-amber-tint text-amber-ink"
          label="Action items"
          count={data.action_items.length}
        />
        <ul className="mt-4 space-y-2.5">
          {data.action_items.length ? (
            data.action_items.map((item, i) => {
              const hasDue =
                item.due && item.due.toLowerCase() !== "unspecified";
              const unassigned = item.owner === "Unassigned";
              return (
                <li
                  key={i}
                  className="rounded-xl border border-border bg-muted/40 p-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <span
                        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full font-mono text-[10px] font-bold ${
                          unassigned
                            ? "bg-secondary text-muted-foreground"
                            : "bg-brand-tint text-brand"
                        }`}
                      >
                        {unassigned ? "—" : initials(item.owner)}
                      </span>
                      <p
                        className={`truncate text-sm font-semibold ${
                          unassigned ? "text-muted-foreground" : ""
                        }`}
                      >
                        {item.owner}
                      </p>
                    </div>
                    {hasDue ? (
                      <span className="shrink-0 rounded-full bg-amber-tint px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-amber-ink">
                        due {item.due}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-2 text-sm leading-snug text-body">
                    {item.task}
                  </p>
                </li>
              );
            })
          ) : (
            <li className="text-xs text-muted-foreground">
              No action items extracted.
            </li>
          )}
        </ul>
      </section>

      {data.derived_document_id ? (
        <div className="flex items-start gap-2.5 rounded-xl border border-border bg-muted/40 p-4 text-xs text-muted-foreground">
          <FileText className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          <p>
            The structured body of this meeting is also indexed as a searchable
            document.{" "}
            <Link
              href={`/documents?focus=${data.derived_document_id}`}
              className="font-medium text-brand hover:underline"
            >
              Open &ldquo;{data.derived_document_name ?? "Meeting summary"}&rdquo;
            </Link>
            .
          </p>
        </div>
      ) : null}
    </div>
  );
}

function SectionHeader({
  icon: Icon,
  tint,
  label,
  count,
}: {
  icon: typeof Users;
  tint: string;
  label: string;
  count?: number;
}) {
  return (
    <header className="flex items-center gap-2.5">
      <span
        className={`flex h-8 w-8 items-center justify-center rounded-lg ${tint}`}
      >
        <Icon className="h-4 w-4" />
      </span>
      <h2 className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        {label}
      </h2>
      {count !== undefined ? (
        <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[11px] font-bold text-body">
          {count}
        </span>
      ) : null}
    </header>
  );
}
