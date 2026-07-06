"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Users, Lock, Globe, Loader2, UserPlus, LogOut } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { MemberPicker } from "@/components/channels/member-picker";
import { ManageMembersDialog } from "@/components/channels/manage-members-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Channel {
  id: string;
  title: string;
  topic: string | null;
  is_channel: boolean;
  channel_visibility: "private" | "org";
  member_count: number;
  my_role: "owner" | "editor" | "viewer" | null;
  updated_at: string;
}

// Actual role badges — one status hue each, drawn from the design system's
// tint palette rather than raw Tailwind colours.
const ROLE_BADGE: Record<NonNullable<Channel["my_role"]>, string> = {
  owner: "bg-violet-tint text-violet",
  editor: "bg-brand-tint text-brand",
  viewer: "bg-muted text-muted-foreground",
};

export default function ChannelsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [visibility, setVisibility] = useState<"private" | "org">("private");
  const [inviteeIds, setInviteeIds] = useState<string[]>([]);
  const [inviteTarget, setInviteTarget] = useState<Channel | null>(null);
  const [meId, setMeId] = useState<string | null>(null);
  const [leaveTarget, setLeaveTarget] = useState<Channel | null>(null);
  const [leaving, setLeaving] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch("/api/channels");
      const data = await res.json();
      setChannels((data.channels as Channel[]) ?? []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    void (async () => {
      try {
        const res = await fetch("/api/me");
        const data = await res.json();
        setMeId(data?.user?.id ?? null);
      } catch {
        // Non-fatal: Leave falls back to a disabled state without an id.
      }
    })();
  }, []);

  async function leave() {
    if (!leaveTarget || !meId) return;
    setLeaving(true);
    try {
      const res = await fetch(
        `/api/channels/${leaveTarget.id}/participants/${meId}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          err.detail === "last_owner_cannot_leave"
            ? "You're the only owner — make someone else an owner first."
            : err.detail || `Couldn't leave (${res.status})`,
        );
      }
      toast.success(`You left "${leaveTarget.title}".`);
      setLeaveTarget(null);
      await load();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Couldn't leave channel.");
    } finally {
      setLeaving(false);
    }
  }

  async function create() {
    if (!title.trim()) return;
    setCreating(true);
    try {
      const res = await fetch("/api/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          topic: topic.trim() || null,
          visibility,
          invitee_user_ids: inviteeIds,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Create failed (${res.status})`);
      }
      const created = await res.json();
      toast.success(`Channel "${created.title}" created.`);
      setTitle("");
      setTopic("");
      setVisibility("private");
      setInviteeIds([]);
      setOpen(false);
      await load();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Couldn't create channel.";
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-6 md:p-8">
      <div className="mb-7 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight text-foreground">
            Channels
          </h1>
          <p className="mt-1.5 text-[15px] leading-relaxed text-muted-foreground">
            Shared, multi-user conversations your whole workspace can post to.
          </p>
          {!loading && channels.length > 0 && (
            <p className="mt-2 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
              {channels.length} channel{channels.length === 1 ? "" : "s"}
            </p>
          )}
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="size-4 mr-2" />
              New channel
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create channel</DialogTitle>
              <DialogDescription>
                Channels host a shared conversation everyone can post to.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  Title
                </label>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. RFP — Acme Inc."
                  maxLength={200}
                  autoFocus
                />
              </div>
              <div>
                <label className="mb-1.5 block font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  Topic <span className="text-muted-foreground/60">(optional)</span>
                </label>
                <Textarea
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="What is this channel about?"
                  rows={2}
                  maxLength={1000}
                />
              </div>
              <div>
                <label className="mb-1.5 block font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  Visibility
                </label>
                <Select
                  value={visibility}
                  onValueChange={(v) => setVisibility(v as "private" | "org")}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">
                      <span className="flex items-center gap-2">
                        <Lock className="size-3.5" /> Private — invitees only
                      </span>
                    </SelectItem>
                    <SelectItem value="org">
                      <span className="flex items-center gap-2">
                        <Globe className="size-3.5" /> Workspace — anyone can join
                      </span>
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="mb-1.5 block font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  Invite people{" "}
                  <span className="text-muted-foreground/60">(optional)</span>
                </label>
                <MemberPicker selected={inviteeIds} onChange={setInviteeIds} />
                <p className="mt-1.5 text-xs text-muted-foreground">
                  {visibility === "private"
                    ? "Only you and the people you add can see this channel."
                    : "Anyone in your workspace can join, but these people are added right away."}
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setOpen(false)} disabled={creating}>
                Cancel
              </Button>
              <Button onClick={create} disabled={creating || !title.trim()}>
                {creating ? <Loader2 className="size-4 mr-2 animate-spin" /> : null}
                Create
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      ) : channels.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-background px-6 py-16 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-tint text-brand">
            <Users className="size-5" />
          </div>
          <h2 className="text-base font-bold text-foreground">No channels yet</h2>
          <p className="mx-auto mt-1 max-w-xs text-sm text-muted-foreground">
            Create a channel to give your team a shared conversation with full
            company context.
          </p>
        </div>
      ) : (
        <ul className="space-y-2.5">
          {channels.map((c) => {
            const isOrg = c.channel_visibility === "org";
            return (
              <li key={c.id}>
                <Link
                  href={`/chat/${c.id}`}
                  className="group flex items-center gap-3.5 rounded-2xl border border-border bg-background p-3.5 transition-all hover:border-input hover:shadow-[0_2px_10px_-4px_rgba(16,24,40,0.12)]"
                >
                  <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-brand-tint font-mono text-lg font-extrabold text-brand">
                    #
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-bold text-foreground">
                        {c.title}
                      </span>
                      <span
                        className={cn(
                          "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
                          isOrg
                            ? "bg-brand-tint text-brand"
                            : "bg-muted text-muted-foreground",
                        )}
                      >
                        {isOrg ? (
                          <Globe className="size-2.5" />
                        ) : (
                          <Lock className="size-2.5" />
                        )}
                        {isOrg ? "Workspace" : "Private"}
                      </span>
                    </div>
                    {c.topic ? (
                      <p className="mt-0.5 truncate text-sm text-muted-foreground">
                        {c.topic}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {c.my_role === "owner" ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setInviteTarget(c);
                        }}
                        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs font-semibold text-body opacity-0 transition-all hover:border-input hover:bg-muted group-hover:opacity-100"
                        title="Manage members"
                      >
                        <UserPlus className="size-3.5" />
                        Members
                      </button>
                    ) : c.my_role ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setLeaveTarget(c);
                        }}
                        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs font-semibold text-body opacity-0 transition-all hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                        title="Leave channel"
                      >
                        <LogOut className="size-3.5" />
                        Leave
                      </button>
                    ) : null}
                    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs font-semibold text-body tabular-nums">
                      <Users className="size-3.5 text-muted-foreground" />
                      {c.member_count}
                    </span>
                    {c.my_role ? (
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide",
                          ROLE_BADGE[c.my_role],
                        )}
                      >
                        {c.my_role}
                      </span>
                    ) : null}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {inviteTarget ? (
        <ManageMembersDialog
          channelId={inviteTarget.id}
          channelTitle={inviteTarget.title}
          myRole={inviteTarget.my_role}
          open={!!inviteTarget}
          onOpenChange={(o) => {
            if (!o) setInviteTarget(null);
          }}
          onChanged={() => void load()}
        />
      ) : null}

      <Dialog
        open={!!leaveTarget}
        onOpenChange={(o) => {
          if (!o && !leaving) setLeaveTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Leave channel?</DialogTitle>
            <DialogDescription>
              You&apos;ll be removed from{" "}
              <span className="font-semibold text-body">
                {leaveTarget?.title}
              </span>
              . An owner can add you back later.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setLeaveTarget(null)}
              disabled={leaving}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={leave}
              disabled={leaving || !meId}
            >
              {leaving ? <Loader2 className="size-4 mr-2 animate-spin" /> : null}
              Leave
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
