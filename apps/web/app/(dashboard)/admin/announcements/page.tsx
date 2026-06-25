"use client";

import Link from "next/link";
import useSWR from "swr";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

interface AnnouncementRow {
  id: string;
  request_text: string;
  status: string;
  scheduled_for: string | null;
  sent_at: string | null;
  created_at: string;
}

interface ListResponse {
  announcements: AnnouncementRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-700",
  scheduled: "bg-blue-100 text-blue-700",
  sending: "bg-amber-100 text-amber-700",
  sent: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-zinc-100 text-zinc-500",
};

export default function AnnouncementsPage() {
  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/admin/announcements",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Internal announcements
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            One prompt → email + Slack + Notion versions, scheduled to fire together.
          </p>
        </div>
        <Button asChild>
          <Link href="/admin/announcements/new" className="gap-2">
            <Plus className="size-4" />
            New announcement
          </Link>
        </Button>
      </header>

      {error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          {(error as Error).message}
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : !data?.announcements.length ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          No announcements yet. Click <strong>New announcement</strong> to draft one.
        </div>
      ) : (
        <ul className="space-y-2">
          {data.announcements.map((a) => (
            <li key={a.id}>
              <Link
                href={`/admin/announcements/${a.id}`}
                className="flex items-center justify-between gap-3 rounded-md border bg-card p-3 hover:bg-accent"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{a.request_text}</p>
                  <p className="text-xs text-muted-foreground">
                    {a.sent_at
                      ? `Sent ${new Date(a.sent_at).toLocaleString()}`
                      : a.scheduled_for
                        ? `Scheduled ${new Date(a.scheduled_for).toLocaleString()}`
                        : `Drafted ${new Date(a.created_at).toLocaleString()}`}
                  </p>
                </div>
                <Badge className={STATUS_COLORS[a.status] ?? "bg-zinc-100 text-zinc-700"}>
                  {a.status}
                </Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
