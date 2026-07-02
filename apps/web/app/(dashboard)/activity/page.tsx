"use client";

import useSWR from "swr";
import { Users } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatDistanceToNow } from "@/lib/date";

interface ActivityItem {
  id: string;
  user_id: string;
  user_name: string;
  user_email: string | null;
  activity_type:
    | "generated_content"
    | "uploaded_doc"
    | "shared_output"
    | "used_template";
  summary: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

const fetcher = async (url: string): Promise<ActivityItem[]> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load activity (${res.status})`);
  return res.json();
};

export default function TeamActivityPage() {
  const { data, error, isLoading } = useSWR<ActivityItem[]>(
    "/api/team/activity?limit=100",
    fetcher,
    { refreshInterval: 30_000, revalidateOnFocus: true },
  );

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">
            Team activity
          </h1>
          <p className="mt-1.5 text-[15px] text-muted-foreground">
            What teammates have been generating, uploading, and sharing across
            the workspace.
          </p>
        </div>
        <div className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-brand-tint text-brand">
          <Users className="h-5 w-5" />
        </div>
      </header>

      {isLoading ? (
        <ActivitySkeleton />
      ) : error ? (
        <div className="rounded-2xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          {(error as Error).message}
        </div>
      ) : !data || data.length === 0 ? (
        <p className="rounded-2xl border border-border bg-background px-4 py-8 text-center text-sm text-muted-foreground">
          No activity yet. As your team uploads documents and generates content,
          it&apos;ll show up here.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-2xl border border-border bg-background">
          {data.map((item) => (
            <li key={item.id} className="flex items-start gap-3 px-4 py-3">
              <Avatar name={item.user_name} />
              <div className="min-w-0 flex-1 leading-tight">
                <p className="text-sm">
                  <span className="font-medium">{item.user_name}</span>{" "}
                  <span className="text-muted-foreground">{item.summary}</span>
                </p>
                <p className="mt-0.5 font-mono text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
                  {formatDistanceToNow(item.created_at)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActivitySkeleton() {
  return (
    <ul className="divide-y divide-border rounded-2xl border border-border bg-background">
      {Array.from({ length: 6 }).map((_, i) => (
        <li key={i} className="flex items-start gap-3 px-4 py-3">
          <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton
              className="h-3.5"
              style={{ width: `${55 + ((i * 13) % 35)}%` }}
            />
            <Skeleton className="h-2.5 w-20" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function Avatar({ name }: { name: string }) {
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-tint text-xs font-extrabold text-brand">
      {initials || "·"}
    </span>
  );
}
