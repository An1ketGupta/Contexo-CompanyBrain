"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CalendarClock,
  ExternalLink,
  FileText,
  HelpCircle,
  History,
  Loader2,
  RefreshCw,
  Sparkles,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface PrepBrief {
  executive_summary?: string;
  attendee_context?: string;
  topic_research?: string;
  suggested_questions?: string[];
  source_documents?: { document_id: string; document_name: string }[];
  prior_meeting?: { title: string | null; date: string | null } | null;
}
interface Meeting {
  id: string;
  title: string | null;
  description: string | null;
  start_time: string;
  end_time: string;
  attendee_emails: string[];
  meeting_url: string | null;
  prep_brief: PrepBrief | null;
  prep_brief_status: string;
  prep_brief_error: string | null;
}

const fetcher = async (url: string): Promise<Meeting> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

// Deterministic avatar tint per attendee so the same person keeps one colour.
const ATTENDEE_TINTS = [
  "bg-brand-tint text-brand",
  "bg-violet-tint text-violet",
  "bg-amber-tint text-amber-ink",
  "bg-success-tint text-success-ink",
];

function attendeeTint(email: string): string {
  let h = 0;
  for (let i = 0; i < email.length; i++) h = (h * 31 + email.charCodeAt(i)) >>> 0;
  return ATTENDEE_TINTS[h % ATTENDEE_TINTS.length];
}

// Turn "mnk.gupta2@gmail.com" → "Mnk Gupta" for a readable label.
function attendeeName(email: string): string {
  const local = email.split("@")[0] ?? email;
  const words = local.split(/[._\-+]+/).filter(Boolean);
  if (!words.length) return email;
  return words
    .map((w) => w.replace(/\d+/g, ""))
    .filter(Boolean)
    .map((w) => w[0]!.toUpperCase() + w.slice(1))
    .join(" ") || email;
}

function attendeeInitials(email: string): string {
  const name = attendeeName(email);
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const src = parts.length ? parts : [email];
  return src
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

const STATUS_BADGE: Record<string, string> = {
  ready: "bg-success-tint text-success-ink",
  generating: "bg-amber-tint text-amber-ink",
  pending: "bg-secondary text-muted-foreground",
  failed: "bg-destructive-soft text-destructive-ink",
  skipped: "bg-secondary text-muted-foreground",
};

const STATUS_LABEL: Record<string, string> = {
  ready: "Brief ready",
  generating: "Generating…",
  pending: "Brief pending",
  failed: "Failed",
  skipped: "Skipped",
};

export default function MeetingDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const { data, error, isLoading, mutate } = useSWR<Meeting>(
    id ? `/api/calendar/meetings/${id}` : null,
    fetcher,
    {
      revalidateOnFocus: false,
      refreshInterval: (latest) =>
        latest?.prep_brief_status === "generating" ? 4000 : 0,
    },
  );

  const [generating, setGenerating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleGenerate = async () => {
    if (!id) return;
    setActionError(null);
    setGenerating(true);
    try {
      const res = await fetch(`/api/calendar/meetings/${id}/brief`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      await mutate();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-4 p-6 md:p-8">
        <Skeleton className="h-8 w-64 rounded-lg" />
        <Skeleton className="h-4 w-80 rounded" />
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    );
  }
  if (error || !data)
    return (
      <div className="mx-auto max-w-4xl p-6 md:p-8">
        <div className="rounded-xl border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive-ink">
          Failed to load meeting.
        </div>
      </div>
    );

  const pb = data.prep_brief;
  const briefReady = data.prep_brief_status === "ready" && !!pb;
  const isGenerating =
    generating || data.prep_brief_status === "generating";
  const isSkipped = data.prep_brief_status === "skipped" && !isGenerating;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <Link
        href="/meetings"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> Back to meetings
      </Link>

      <header className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-brand">
            <CalendarClock className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-extrabold tracking-tight">
              {data.title ?? "(no title)"}
            </h1>
            <p className="mt-1.5 font-mono text-xs text-muted-foreground">
              {new Date(data.start_time).toLocaleString()} —{" "}
              {new Date(data.end_time).toLocaleTimeString()}
            </p>
            {data.attendee_emails?.length > 0 && (
              <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-muted-foreground">
                  <Users className="h-3.5 w-3.5 shrink-0" />
                  {data.attendee_emails.length}
                </span>
                {data.attendee_emails.map((email) => (
                  <span
                    key={email}
                    title={email}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/60 py-0.5 pl-0.5 pr-2.5 text-xs font-medium text-body"
                  >
                    <span
                      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[9px] font-bold ${attendeeTint(
                        email,
                      )}`}
                    >
                      {attendeeInitials(email)}
                    </span>
                    {attendeeName(email)}
                  </span>
                ))}
              </div>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2.5">
              <span
                className={`rounded-full px-2.5 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide ${
                  STATUS_BADGE[data.prep_brief_status] ??
                  "bg-secondary text-muted-foreground"
                }`}
              >
                {STATUS_LABEL[data.prep_brief_status] ?? data.prep_brief_status}
              </span>
              {briefReady && pb?.prior_meeting && (
                <span className="inline-flex items-center gap-1 rounded-full bg-violet-tint px-2.5 py-0.5 text-[11px] font-medium text-violet">
                  <History className="h-3 w-3 shrink-0" />
                  Continued from last meeting
                  {pb.prior_meeting.date
                    ? ` · ${new Date(pb.prior_meeting.date).toLocaleDateString()}`
                    : ""}
                </span>
              )}
              {data.meeting_url && (
                <a
                  href={data.meeting_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-sm font-medium text-brand hover:underline"
                >
                  Open meeting URL <ExternalLink className="h-3.5 w-3.5" />
                </a>
              )}
            </div>
          </div>
        </div>
        <Button
          onClick={handleGenerate}
          disabled={isGenerating}
          variant="outline"
          size="sm"
          className="shrink-0"
        >
          {isGenerating ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Generating…
            </>
          ) : briefReady ? (
            <>
              <RefreshCw className="h-3.5 w-3.5" /> Regenerate brief
            </>
          ) : (
            <>
              <Sparkles className="h-3.5 w-3.5" /> Generate prep brief
            </>
          )}
        </Button>
      </header>

      {actionError && (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{actionError}</span>
        </div>
      )}
      {data.prep_brief_error && (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{data.prep_brief_error}</span>
        </div>
      )}

      {isGenerating && !briefReady ? (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-brand" />
          Generating your prep brief — this page refreshes automatically.
        </div>
      ) : null}

      {isSkipped ? (
        <div className="rounded-xl border border-dashed border-border bg-card px-6 py-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
            <FileText className="h-5 w-5" />
          </div>
          <p className="mt-3 text-sm font-semibold">No relevant context found</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
            Nothing in your knowledge base matched this meeting&apos;s topic or
            attendees closely enough to ground a brief.
          </p>
        </div>
      ) : null}

      {!briefReady && !isGenerating && !isSkipped && !data.prep_brief_error ? (
        <div className="rounded-xl border border-dashed border-border bg-card px-6 py-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
            <Sparkles className="h-5 w-5" />
          </div>
          <p className="mt-3 text-sm font-semibold">No prep brief yet</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
            Generate a brief grounded in your knowledge base — executive summary,
            attendee context, topic research, and suggested questions.
          </p>
        </div>
      ) : null}

      {briefReady && pb && (
        <section className="divide-y divide-border rounded-xl border border-border bg-card p-6">
          {pb.executive_summary && (
            <BriefBlock
              icon={Sparkles}
              tint="bg-brand-tint text-brand"
              title="Executive summary"
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-body">
                {pb.executive_summary}
              </p>
            </BriefBlock>
          )}
          {pb.attendee_context && (
            <BriefBlock
              icon={Users}
              tint="bg-violet-tint text-violet"
              title="Attendee context"
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-body">
                {pb.attendee_context}
              </p>
            </BriefBlock>
          )}
          {pb.topic_research && (
            <BriefBlock
              icon={BookOpen}
              tint="bg-amber-tint text-amber-ink"
              title="Topic research"
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-body">
                {pb.topic_research}
              </p>
            </BriefBlock>
          )}
          {pb.suggested_questions && pb.suggested_questions.length > 0 && (
            <BriefBlock
              icon={HelpCircle}
              tint="bg-success-tint text-success-ink"
              title="Suggested questions"
            >
              <ol className="space-y-2.5">
                {pb.suggested_questions.map((q, i) => (
                  <li
                    key={i}
                    className="flex gap-3 text-sm leading-snug text-body"
                  >
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-tint font-mono text-[10px] font-bold text-brand">
                      {i + 1}
                    </span>
                    <span>{q}</span>
                  </li>
                ))}
              </ol>
            </BriefBlock>
          )}
          {pb.source_documents && pb.source_documents.length > 0 && (
            <BriefBlock
              icon={FileText}
              tint="bg-secondary text-muted-foreground"
              title="Based on"
            >
              <ul className="flex flex-wrap gap-2">
                {pb.source_documents.map((s) => (
                  <li
                    key={s.document_id}
                    className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-3 py-1 text-xs font-medium text-body"
                  >
                    <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    {s.document_name}
                  </li>
                ))}
              </ul>
            </BriefBlock>
          )}
        </section>
      )}
    </div>
  );
}

function BriefBlock({
  icon: Icon,
  tint,
  title,
  children,
}: {
  icon: typeof Users;
  tint: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="py-5 first:pt-0 last:pb-0">
      <header className="flex items-center gap-2.5">
        <span
          className={`flex h-8 w-8 items-center justify-center rounded-lg ${tint}`}
        >
          <Icon className="h-4 w-4" />
        </span>
        <h3 className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
      </header>
      <div className="mt-3">{children}</div>
    </div>
  );
}
