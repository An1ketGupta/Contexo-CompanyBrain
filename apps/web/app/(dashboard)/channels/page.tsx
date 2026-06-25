"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, Users, Lock, Globe, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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

export default function ChannelsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [visibility, setVisibility] = useState<"private" | "org">("private");

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
  }, []);

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
          invitee_user_ids: [],
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
    <div className="container max-w-4xl py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">Channels</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Multi-user conversations. Everyone sees the same AI thread live.
          </p>
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
            <div className="space-y-3">
              <div>
                <label className="text-sm font-medium block mb-1">Title</label>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. RFP — Acme Inc."
                  maxLength={200}
                  autoFocus
                />
              </div>
              <div>
                <label className="text-sm font-medium block mb-1">Topic (optional)</label>
                <Textarea
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="What is this channel about?"
                  rows={2}
                  maxLength={1000}
                />
              </div>
              <div>
                <label className="text-sm font-medium block mb-1">Visibility</label>
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
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      ) : channels.length === 0 ? (
        <div className="text-center py-12 border rounded-lg">
          <Users className="size-10 mx-auto text-muted-foreground mb-2" />
          <p className="text-muted-foreground">No channels yet. Create one to start.</p>
        </div>
      ) : (
        <ul className="space-y-2">
          {channels.map((c) => (
            <li key={c.id}>
              <Link
                href={`/chat/${c.id}`}
                className="flex items-center justify-between p-4 border rounded-lg hover:bg-accent/40 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    {c.channel_visibility === "org" ? (
                      <Globe className="size-3.5 text-muted-foreground" />
                    ) : (
                      <Lock className="size-3.5 text-muted-foreground" />
                    )}
                    <span className="font-medium truncate">{c.title}</span>
                  </div>
                  {c.topic ? (
                    <p className="text-sm text-muted-foreground truncate mt-0.5">
                      {c.topic}
                    </p>
                  ) : null}
                </div>
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Users className="size-3.5" /> {c.member_count}
                  </span>
                  {c.my_role ? (
                    <span className="px-2 py-0.5 rounded-full bg-muted text-xs">
                      {c.my_role}
                    </span>
                  ) : null}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
