"use client";

import { useState } from "react";

import Link from "next/link";
import useSWR from "swr";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

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
  draft: "bg-muted text-muted-foreground",
  scheduled: "bg-brand-tint text-brand",
  sending: "bg-amber-tint text-amber-ink",
  sent: "bg-success-tint text-success-ink",
  failed: "bg-destructive-soft text-destructive-ink",
  cancelled: "bg-muted text-muted-foreground",
};

export default function AnnouncementsPage() {
  const { data, error, isLoading, mutate } = useSWR<ListResponse>(
    "/api/admin/announcements",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [deleting, setDeleting] = useState<string | null>(null);

  const handleDelete = async (e: React.MouseEvent, a: AnnouncementRow) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`Delete this announcement?\n\n"${a.request_text.slice(0, 100)}…"`)) return;
    setDeleting(a.id);
    try {
      const res = await fetch(`/api/admin/announcements/${a.id}`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.message || `Failed (${res.status})`);
      }
      toast.success("Announcement deleted.");
      mutate();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">
            Internal announcements
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            One Announcements → Email + Slack + Notion.
          </p>
        </div>
        <Button asChild className="rounded-full">
          <Link href="/admin/announcements/new" className="gap-2">
            <Plus className="size-4" />
            New announcement
          </Link>
        </Button>
      </header>

      {error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive-ink">
          {(error as Error).message}
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-2xl" />
          ))}
        </div>
      ) : !data?.announcements.length ? (
        <div className="rounded-2xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          No announcements yet. Click <strong>New announcement</strong> to draft one.
        </div>
      ) : (
        <ul className="space-y-2">
          {data.announcements.map((a) => (
            <li key={a.id}>
              <Link
                href={`/admin/announcements/${a.id}`}
                className="flex items-center justify-between gap-3 rounded-2xl border border-border bg-card p-3 transition-colors hover:bg-muted/50"
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
                <div className="flex items-center gap-2">
                  <Badge className={STATUS_COLORS[a.status] ?? "bg-muted text-muted-foreground"}>
                    {a.status}
                  </Badge>
                  {["draft", "cancelled", "failed"].includes(a.status) && (
                    <button
                      type="button"
                      onClick={(e) => handleDelete(e, a)}
                      disabled={deleting === a.id}
                      className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-destructive-soft hover:text-destructive-ink disabled:opacity-50"
                      aria-label="Delete announcement"
                    >
                      {deleting === a.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Trash2 className="size-4" />
                      )}
                    </button>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
