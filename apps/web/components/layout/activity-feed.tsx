"use client";

import useSWR from "swr";
import { Activity } from "lucide-react";

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

/**
 * Sidebar widget for V4 #57. SWR polls every 30s while the tab is visible —
 * we tolerate eventually-consistent reads because the feed is a sidebar
 * affordance, not the primary surface.
 */
export function ActivityFeed() {
  const { data } = useSWR<ActivityItem[]>("/api/team/activity?limit=12", fetcher, {
    refreshInterval: 30_000,
    revalidateOnFocus: true,
  });

  if (!data || data.length === 0) return null;

  return (
    <div className="border-t border-border px-3 py-3">
      <p className="mb-2 flex items-center gap-1.5 px-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
        <Activity className="h-3 w-3" />
        Team activity
      </p>
      <ul className="space-y-2.5 max-h-48 overflow-y-auto pr-1">
        {data.map((item) => (
          <li key={item.id} className="flex items-start gap-2">
            <Avatar name={item.user_name} />
            <div className="min-w-0 flex-1 leading-tight">
              <p className="text-xs">
                <span className="font-medium">
                  {firstName(item.user_name)}
                </span>{" "}
                <span className="text-muted-foreground">{item.summary}</span>
              </p>
              <p className="mt-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                {formatDistanceToNow(item.created_at)}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function firstName(full: string): string {
  return full.split(" ")[0] || full;
}

function Avatar({ name }: { name: string }) {
  const initials = name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
      {initials || "·"}
    </span>
  );
}
