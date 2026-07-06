"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { dateKey, MeetingsCalendarView } from "./calendar-view";

export interface Meeting {
  id: string;
  title: string | null;
  start_time: string;
  end_time: string;
  attendee_emails: string[];
  meeting_url: string | null;
  prep_brief_status: "pending" | "generating" | "ready" | "failed" | "skipped";
  prep_brief_available_at: string | null;
}

interface GoogleWorkspaceStatus {
  connected: boolean;
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_BADGE: Record<string, string> = {
  ready: "bg-success-tint text-success",
  generating: "bg-amber-tint text-amber",
  pending: "bg-secondary text-muted-foreground",
  failed: "bg-destructive-soft text-destructive",
  skipped: "bg-secondary text-muted-foreground",
};

export function UpcomingMeetingsPanel() {
  const { data, error, isLoading, mutate } = useSWR<{ meetings: Meeting[] }>(
    "/api/calendar/meetings",
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: gwStatus } = useSWR<GoogleWorkspaceStatus>(
    "/api/integrations/google-workspace/status",
    fetcher,
  );
  const [syncing, setSyncing] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const handleSync = async () => {
    setSyncing(true);
    try {
      await fetch("/api/calendar/meetings/sync", { method: "POST" });
      await mutate();
    } finally {
      setSyncing(false);
    }
  };

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const res = await fetch("/api/integrations/google-workspace/connect");
      if (!res.ok) return;
      const { auth_url } = await res.json();
      window.location.href = auth_url;
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Automatically generates meeting prep briefs 5 hours before every meeting.
        </p>
        {gwStatus && !gwStatus.connected ? (
          <Button onClick={handleConnect} disabled={connecting}>
            {connecting ? "Connecting…" : "Connect Google Workspace"}
          </Button>
        ) : (
          <Button variant="outline" onClick={handleSync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync now"}
          </Button>
        )}
      </div>

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">
          Failed to load meetings.
        </div>
      ) : (
        <>
          <MeetingsCalendarView
            meetings={data?.meetings ?? []}
            selectedDay={selectedDay}
            onSelectDay={setSelectedDay}
          />
          {!data?.meetings?.length ? (
            <div className="rounded-xl border border-dashed border-border bg-background p-10 text-center text-sm text-muted-foreground">
              No upcoming meetings. Connect Google Workspace to sync your calendar.
            </div>
          ) : (
            renderMeetingsList(data.meetings, selectedDay, setSelectedDay)
          )}
        </>
      )}
    </div>
  );
}

function renderMeetingsList(
  meetings: Meeting[],
  selectedDay: string | null,
  onSelectDay: (key: string | null) => void,
) {
  const filtered = selectedDay
    ? meetings.filter((m) => dateKey(new Date(m.start_time)) === selectedDay)
    : meetings;

  return (
    <div className="space-y-3">
      {selectedDay && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Showing meetings for{" "}
            {(() => {
              const [y, m, d] = selectedDay.split("-").map(Number);
              return new Date(y, m, d).toLocaleDateString(undefined, {
                weekday: "long",
                month: "long",
                day: "numeric",
              });
            })()}
          </p>
          <button
            type="button"
            onClick={() => onSelectDay(null)}
            className="text-xs font-medium text-brand hover:underline"
          >
            Show all
          </button>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-background p-10 text-center text-sm text-muted-foreground">
          No meetings on this day.
        </div>
      ) : (
        <ul className="space-y-3">
          {filtered.map((m) => (
            <li
              key={m.id}
              className="rounded-xl border border-border bg-card p-4 transition-colors hover:border-input hover:bg-muted"
            >
              <Link href={`/calendar/${m.id}`} className="block">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate font-medium">{m.title ?? "(no title)"}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {new Date(m.start_time).toLocaleString()}
                      {m.attendee_emails?.length > 0 && (
                        <> · {m.attendee_emails.length} attendee
                          {m.attendee_emails.length === 1 ? "" : "s"}</>
                      )}
                    </p>
                  </div>
                  <Badge className={STATUS_BADGE[m.prep_brief_status] ?? ""}>
                    {m.prep_brief_status === "ready"
                      ? "Brief ready"
                      : m.prep_brief_status === "generating"
                        ? "Generating…"
                        : m.prep_brief_status === "pending"
                          ? "Brief pending"
                          : m.prep_brief_status}
                  </Badge>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
