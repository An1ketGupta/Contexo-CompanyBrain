"use client";

import { use } from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, ArrowLeft, Calendar, Clock, Loader2 } from "lucide-react";

import { Markdown } from "@/components/chat/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";

interface Briefing {
  id: string;
  status: "generating" | "ok" | "failed";
  error_message: string | null;
  summary: string | null;
  body_md: string | null;
  data: Record<string, unknown>;
  period_key: string;
  created_at: string;
}

interface SingleResponse {
  briefing: Briefing;
}

const fetcher = async (url: string): Promise<SingleResponse> => {
  const res = await fetch(url);
  if (res.status === 404) throw new Error("Briefing not found.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function BriefingDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data, error, isLoading } = useSWR<SingleResponse>(
    `/api/briefings/${id}`,
    fetcher,
    {
      revalidateOnFocus: false,
      // If the briefing is still generating, poll every 8s — fast enough to
      // feel live, slow enough not to load the API.
      refreshInterval: (resp) =>
        resp?.briefing?.status === "generating" ? 8_000 : 0,
    },
  );

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6 md:p-8">
      <Link
        href="/briefings"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" />
        All briefings
      </Link>

      {error ? (
        <div className="flex items-start gap-2 rounded-xl border border-destructive/40 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error.message}</span>
        </div>
      ) : null}

      {isLoading || !data ? (
        <div className="space-y-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
        </div>
      ) : (
        <BriefingBody briefing={data.briefing} />
      )}
    </div>
  );
}

function BriefingBody({ briefing }: { briefing: Briefing }) {
  if (briefing.status === "generating") {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-center">
        <Loader2 className="mx-auto h-6 w-6 animate-spin text-brand" />
        <h2 className="mt-3 text-sm font-medium">Generating your briefing…</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          This usually finishes in under a minute.
        </p>
      </div>
    );
  }

  if (briefing.status === "failed") {
    return (
      <div className="rounded-xl border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive-ink">
        <p className="font-medium">We couldn&apos;t generate this briefing.</p>
        {briefing.error_message && (
          <p className="mt-1 text-xs">{briefing.error_message}</p>
        )}
      </div>
    );
  }

  return (
    <article className="space-y-4">
      <header className="flex items-start gap-3">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-brand">
          <Calendar className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">
            {briefing.period_key}
          </h1>
          <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Clock className="h-3.5 w-3.5" />
            {formatDistanceToNow(briefing.created_at)} ago
          </p>
        </div>
      </header>

      <div className="rounded-xl border border-border bg-card p-6">
        {briefing.body_md ? (
          <Markdown>{briefing.body_md}</Markdown>
        ) : (
          <p className="text-sm text-muted-foreground">
            {briefing.summary || "No content."}
          </p>
        )}
      </div>
    </article>
  );
}
