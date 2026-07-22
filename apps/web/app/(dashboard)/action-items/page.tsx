"use client";

import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { toast } from "sonner";
import { AlertTriangle, CalendarClock, Check, ListChecks, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { SlackChannelPicker } from "@/components/action-items/slack-channel-picker";
import { cn } from "@/lib/utils";

interface ActionItem {
  id: string;
  action_text: string;
  owner_name_raw: string | null;
  owner_user_id: string | null;
  due_date: string | null;
  status: string;
}

const fetcher = async (url: string): Promise<{ action_items: ActionItem[] }> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

// Deterministic avatar tint per owner so the same person keeps one colour.
const AVATAR_TINTS = [
  "bg-brand-tint text-brand",
  "bg-violet-tint text-violet",
  "bg-amber-tint text-amber-ink",
  "bg-success-tint text-success-ink",
];

function avatarTint(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_TINTS[h % AVATAR_TINTS.length];
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return parts
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

interface DueMeta {
  label: string;
  tone: string;
  overdue: boolean;
}

// Formats the due date with urgency-aware colouring so overdue / imminent
// items stand out instead of every row reading the same.
function dueMeta(due: string | null): DueMeta | null {
  if (!due) return null;
  const d = new Date(`${due}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((d.getTime() - today.getTime()) / 86_400_000);
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  if (diffDays < 0)
    return { label: `Overdue · ${date}`, tone: "bg-destructive-soft text-destructive-ink", overdue: true };
  if (diffDays === 0)
    return { label: "Due today", tone: "bg-amber-tint text-amber-ink", overdue: false };
  if (diffDays <= 3)
    return { label: `Due ${date}`, tone: "bg-amber-tint text-amber-ink", overdue: false };
  return { label: `Due ${date}`, tone: "bg-secondary text-muted-foreground", overdue: false };
}

// Earliest due date first; items with no due date sink to the bottom.
function byDueDate(a: ActionItem, b: ActionItem): number {
  if (!a.due_date && !b.due_date) return 0;
  if (!a.due_date) return 1;
  if (!b.due_date) return -1;
  return a.due_date.localeCompare(b.due_date);
}

const STATUS_PILL: Record<string, { label: string; bg: string; dot: string }> = {
  pending: { label: "Pending", bg: "bg-secondary text-muted-foreground", dot: "bg-muted-foreground/50" },
  completed: { label: "Completed", bg: "bg-success-tint text-success-ink", dot: "bg-success" },
  cancelled: { label: "Cancelled", bg: "bg-secondary text-muted-foreground", dot: "bg-muted-foreground/40" },
};

function MarkDoneButton({
  busy,
  onClick,
}: {
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      size="sm"
      variant="outline"
      className="shrink-0 bg-card"
      onClick={onClick}
      disabled={busy}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <Check className="h-3.5 w-3.5" />
      )}
      Mark done
    </Button>
  );
}

function StatusPill({ status }: { status: string }) {
  const s = STATUS_PILL[status] ?? STATUS_PILL.pending;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold",
        s.bg,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
      {s.label}
    </span>
  );
}

function ActionItemRow({
  item,
  trailing,
}: {
  item: ActionItem;
  trailing?: React.ReactNode;
}) {
  const due = dueMeta(item.due_date);
  const owner = item.owner_name_raw?.trim() || null;
  const done = item.status === "completed" || item.status === "cancelled";
  return (
    <li className="group flex items-center gap-3.5 rounded-xl border border-border bg-card p-3.5 transition-colors hover:border-brand/40 hover:bg-muted/30">
      <span
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-bold",
          owner ? avatarTint(owner) : "bg-secondary text-muted-foreground",
        )}
      >
        {owner ? initials(owner) : "—"}
      </span>

      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "truncate text-sm font-medium text-foreground",
            done && "text-muted-foreground line-through",
          )}
        >
          {item.action_text}
        </p>
        <p className="mt-1 truncate text-xs font-medium text-foreground/70">
          {owner ?? "Unassigned"}
        </p>
      </div>

      {due ? (
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold",
            due.tone,
          )}
        >
          {due.overdue ? (
            <AlertTriangle className="h-3.5 w-3.5" />
          ) : (
            <CalendarClock className="h-3.5 w-3.5" />
          )}
          {due.label}
        </span>
      ) : null}

      {trailing ?? <StatusPill status={item.status} />}
    </li>
  );
}

// Backend PermissionError codes → friendly copy.
const SLACK_ERRORS: Record<string, string> = {
  slack_not_connected:
    "Slack isn't connected. Connect it in Settings → Integrations.",
  not_in_channel:
    "The NirnayaIQ bot isn't in that channel — invite it, then try again.",
  channel_not_found: "That channel wasn't found.",
  token_revoked: "Slack access was revoked. Reconnect Slack.",
};

export default function ActionItemsPage() {
  const { data, isLoading } = useSWR<{ action_items: ActionItem[] }>(
    "/api/action-items",
    fetcher,
    { revalidateOnFocus: false },
  );
  const { mutate } = useSWRConfig();

  const [notes, setNotes] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractedItems, setExtractedItems] = useState<ActionItem[]>([]);
  const [doneBusyId, setDoneBusyId] = useState<string | null>(null);
  const [channelId, setChannelId] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExtract = async () => {
    setError(null);
    setExtracting(true);
    try {
      const res = await fetch("/api/action-items/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Failed (${res.status})`);
      }
      const body = await res.json();
      setExtractedItems(body.action_items ?? []);
      setNotes("");
      await mutate("/api/action-items");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Extract failed");
    } finally {
      setExtracting(false);
    }
  };

  const handleMarkDone = async (id: string) => {
    setDoneBusyId(id);
    try {
      const res = await fetch(`/api/action-items/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "completed" }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Failed (${res.status})`);
      }
      setExtractedItems((prev) => prev.filter((i) => i.id !== id));
      await mutate(
        "/api/action-items",
        (cur: { action_items: ActionItem[] } | undefined) => ({
          action_items: (cur?.action_items ?? []).filter((i) => i.id !== id),
        }),
        { revalidate: false },
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't mark done.");
    } finally {
      setDoneBusyId(null);
    }
  };

  const handlePost = async () => {
    if (!extractedItems.length || !channelId) return;
    setError(null);
    setPosting(true);
    try {
      const res = await fetch("/api/action-items/post-to-slack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_item_ids: extractedItems.map((i) => i.id),
          channel_id: channelId,
        }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        const detail: string = b?.detail || `Failed (${res.status})`;
        throw new Error(SLACK_ERRORS[detail] || detail);
      }
      const body = await res.json();
      toast.success(
        `Posted ${body.posted} action item${body.posted === 1 ? "" : "s"} to Slack.`,
      );
      setExtractedItems([]);
      setChannelId("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Post failed");
    } finally {
      setPosting(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start gap-3">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-brand">
          <ListChecks className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Action items</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Paste meeting notes — we extract owners + due dates and post the list
            to a Slack channel.
          </p>
        </div>
      </header>

      <section className="space-y-4 rounded-xl border border-border bg-card p-6">
        <div className="space-y-2">
          <Label htmlFor="notes">Paste meeting notes</Label>
          <Textarea
            id="notes"
            rows={8}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Bullet points, transcript snippets, anything readable. We&apos;ll pick out commitments."
          />
        </div>

        {error && (
          <div className="rounded-xl border border-destructive/40 bg-destructive-soft p-3 text-sm text-destructive-ink">
            {error}
          </div>
        )}

        <div className="flex justify-end">
          <Button onClick={handleExtract} disabled={extracting || notes.trim().length < 10}>
            {extracting ? "Extracting…" : "Extract action items"}
          </Button>
        </div>

        {extractedItems.length > 0 && (
          <div className="space-y-4 rounded-xl border border-amber/30 bg-amber-tint p-4">
            <p className="text-sm font-medium text-foreground">
              {extractedItems.length} item{extractedItems.length === 1 ? "" : "s"}
              {" "}extracted. Mark items done, or post the rest to Slack.
            </p>

            <ul className="space-y-2.5">
              {extractedItems.map((item) => (
                <ActionItemRow
                  key={item.id}
                  item={item}
                  trailing={
                    <MarkDoneButton
                      busy={doneBusyId === item.id}
                      onClick={() => handleMarkDone(item.id)}
                    />
                  }
                />
              ))}
            </ul>

            <div className="flex flex-wrap items-end gap-3 border-t border-amber/30 pt-4">
              <div className="w-64">
                <SlackChannelPicker
                  value={channelId}
                  onChange={setChannelId}
                  required
                />
              </div>
              <Button onClick={handlePost} disabled={posting || !channelId}>
                {posting ? "Posting…" : "Post to Slack"}
              </Button>
            </div>
          </div>
        )}
      </section>

      <section>
        <div className="mb-3 flex items-center gap-2">
          <h2 className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            Recent action items
          </h2>
          {data?.action_items?.length ? (
            <span className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[10px] font-bold text-body">
              {data.action_items.length}
            </span>
          ) : null}
        </div>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !data?.action_items?.length ? (
          <div className="rounded-xl border border-dashed border-border bg-background p-10 text-center">
            <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-brand-tint text-brand">
              <ListChecks className="h-5 w-5" />
            </div>
            <p className="mt-3 text-sm font-medium">No action items yet</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Paste meeting notes above and we&apos;ll pull out the commitments.
            </p>
          </div>
        ) : (
          <ul className="space-y-2.5">
            {[...data.action_items].sort(byDueDate).map((a) => (
              <ActionItemRow
                key={a.id}
                item={a}
                trailing={
                  a.status === "pending" ? (
                    <MarkDoneButton
                      busy={doneBusyId === a.id}
                      onClick={() => handleMarkDone(a.id)}
                    />
                  ) : undefined
                }
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
