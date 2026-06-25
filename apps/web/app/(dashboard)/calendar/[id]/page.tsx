"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

interface PrepBrief {
  executive_summary?: string;
  attendee_context?: string;
  topic_research?: string;
  suggested_questions?: string[];
  source_documents?: { document_id: string; document_name: string }[];
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
  const [sharing, setSharing] = useState(false);
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

  const handleShare = async () => {
    if (!id) return;
    setActionError(null);
    setSharing(true);
    try {
      const res = await fetch(`/api/calendar/meetings/${id}/share`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Share failed");
    } finally {
      setSharing(false);
    }
  };

  if (isLoading) return <Skeleton className="m-6 h-60 w-full max-w-5xl" />;
  if (error || !data)
    return <div className="m-6 text-sm text-red-700">Failed to load meeting.</div>;

  const pb = data.prep_brief;
  const briefReady = data.prep_brief_status === "ready" && pb;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          {data.title ?? "(no title)"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {new Date(data.start_time).toLocaleString()} —{" "}
          {new Date(data.end_time).toLocaleTimeString()}
        </p>
        {data.attendee_emails?.length > 0 && (
          <p className="mt-1 text-xs text-muted-foreground">
            With: {data.attendee_emails.join(", ")}
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {data.meeting_url && (
            <a
              href={data.meeting_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-blue-600 hover:underline"
            >
              Open meeting URL ↗
            </a>
          )}
          <Badge>{data.prep_brief_status}</Badge>
        </div>
      </header>

      <div className="flex gap-2">
        <Button onClick={handleGenerate} disabled={generating} variant="outline">
          {generating || data.prep_brief_status === "generating"
            ? "Generating…"
            : briefReady
              ? "Regenerate brief"
              : "Generate prep brief"}
        </Button>
        {briefReady && (
          <Button onClick={handleShare} disabled={sharing}>
            {sharing ? "Sending…" : "Share with attendees"}
          </Button>
        )}
      </div>

      {actionError && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {actionError}
        </div>
      )}
      {data.prep_brief_error && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {data.prep_brief_error}
        </div>
      )}

      {briefReady && pb && (
        <section className="space-y-4 rounded border bg-white p-6">
          {pb.executive_summary && (
            <Block title="Executive summary" text={pb.executive_summary} />
          )}
          {pb.attendee_context && (
            <Block title="Attendee context" text={pb.attendee_context} />
          )}
          {pb.topic_research && (
            <Block title="Topic research" text={pb.topic_research} />
          )}
          {pb.suggested_questions && pb.suggested_questions.length > 0 && (
            <div>
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Suggested questions
              </h3>
              <ul className="mt-2 list-inside list-disc space-y-1 text-sm">
                {pb.suggested_questions.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </div>
          )}
          {pb.source_documents && pb.source_documents.length > 0 && (
            <div>
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Based on
              </h3>
              <ul className="mt-1 text-sm text-zinc-700">
                {pb.source_documents.map((s) => (
                  <li key={s.document_id}>{s.document_name}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Block({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{text}</p>
    </div>
  );
}
