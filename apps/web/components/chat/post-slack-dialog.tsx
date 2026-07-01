"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Hash,
  Loader2,
  Lock,
  RefreshCw,
  Search,
  Send,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

interface PostSlackDialogProps {
  messageId: string;
  defaultBody: string;
  workspaceName: string | null;
  onClose: () => void;
  onPosted: (channelName: string) => void;
}

export function PostSlackDialog({
  messageId,
  defaultBody,
  workspaceName,
  onClose,
  onPosted,
}: PostSlackDialogProps) {
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [loadingChannels, setLoadingChannels] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [selectedChannel, setSelectedChannel] = useState<SlackChannel | null>(null);
  const [filter, setFilter] = useState("");
  const [body, setBody] = useState(defaultBody);
  const [threadTs, setThreadTs] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const loadChannels = async (force: boolean) => {
    if (force) setRefreshing(true);
    else setLoadingChannels(true);
    try {
      const url = force
        ? "/api/integrations/slack/channels?refresh=true"
        : "/api/integrations/slack/channels";
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) {
        setErrorMessage("Couldn't load Slack channels. Try refreshing.");
        return;
      }
      const payload = (await res.json()) as { channels: SlackChannel[] };
      setChannels(payload.channels ?? []);
    } finally {
      setLoadingChannels(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    loadChannels(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visibleChannels = useMemo(() => {
    if (!channels) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return channels;
    return channels.filter((c) => c.name.toLowerCase().includes(q));
  }, [channels, filter]);

  const canPost =
    !!selectedChannel && body.trim().length > 0 && !submitting;

  const handlePost = async () => {
    if (!canPost || !selectedChannel) return;
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const res = await fetch("/api/integrations/slack/post", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: messageId,
          channel_id: selectedChannel.id,
          channel_name: selectedChannel.name,
          text: body,
          thread_ts: threadTs.trim() || null,
          // The user clicked through any competitor warning before opening
          // this dialog; the server-side gate needs the explicit ack to
          // proceed when matches exist.
          acknowledged_warnings: true,
        }),
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as { detail?: string };
        const detail = payload.detail ?? "post_failed";
        if (detail === "slack_not_connected") {
          setErrorMessage("Slack is not connected for this workspace.");
        } else if (detail === "message_already_sent") {
          setErrorMessage("This message has already been delivered.");
        } else if (detail === "message_not_found") {
          setErrorMessage("Could not find the original message. Try refreshing.");
        } else if (detail === "confidence_below_block") {
          setErrorMessage(
            "This answer's confidence is below your workspace's publish threshold. Ask the question again to get a higher-confidence response, or have an admin lower the block threshold in Admin → Confidence.",
          );
        } else if (detail === "outbound_rate_limited") {
          const retry = res.headers.get("Retry-After");
          setErrorMessage(
            retry
              ? `You've hit the per-channel send limit. Try again in ${Math.ceil(Number(retry) / 60)} min.`
              : "You've hit the per-channel send limit. Try again later.",
          );
        } else if (detail === "competitor_match_unacknowledged") {
          setErrorMessage("Competitor mentions in this answer require explicit acknowledgement — please retry.");
        } else {
          setErrorMessage("Couldn't queue the post. Please try again.");
        }
        return;
      }
      onPosted(selectedChannel.name);
    } catch {
      setErrorMessage("Network error — please check your connection and retry.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Post to Slack</DialogTitle>
          {workspaceName && (
            <DialogDescription>
              Workspace: <span className="font-medium text-foreground">{workspaceName}</span>
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Channel</Label>
              <button
                type="button"
                onClick={() => loadChannels(true)}
                disabled={refreshing}
                className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
              >
                <RefreshCw className={cn("h-3 w-3", refreshing && "animate-spin")} />
                Refresh
              </button>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter channels…"
                className="pl-8"
              />
            </div>
            <div className="max-h-44 overflow-y-auto rounded-md border border-input">
              {loadingChannels ? (
                <div className="flex items-center justify-center py-6 text-xs text-muted-foreground">
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Loading channels…
                </div>
              ) : visibleChannels.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {channels === null
                    ? "Couldn't load channels."
                    : channels.length === 0
                    ? "The bot can't see any channels yet — invite it with /invite @company-brain in any channel."
                    : "No channels match this filter."}
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {visibleChannels.map((c) => {
                    const selected = selectedChannel?.id === c.id;
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedChannel(c)}
                          className={cn(
                            "flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors",
                            selected
                              ? "bg-primary/10 text-primary"
                              : "hover:bg-muted",
                          )}
                        >
                          {c.is_private ? (
                            <Lock className="h-3 w-3 text-muted-foreground" />
                          ) : (
                            <Hash className="h-3 w-3 text-muted-foreground" />
                          )}
                          <span className="flex-1 truncate font-medium">
                            {c.name}
                          </span>
                          {!c.is_member && (
                            <span className="rounded bg-amber-tint px-1.5 py-0.5 text-[10px] font-medium text-amber">
                              Invite bot
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="slack-body">Message</Label>
            <Textarea
              id="slack-body"
              rows={8}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              className="text-xs"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="slack-thread">
              Thread timestamp{" "}
              <span className="text-muted-foreground">(optional — reply in thread)</span>
            </Label>
            <Input
              id="slack-thread"
              value={threadTs}
              onChange={(e) => setThreadTs(e.target.value)}
              placeholder="e.g. 1718645100.001234"
            />
          </div>

          {errorMessage && (
            <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{errorMessage}</span>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handlePost} disabled={!canPost}>
            {submitting ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="mr-2 h-3.5 w-3.5" />
            )}
            Post
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
