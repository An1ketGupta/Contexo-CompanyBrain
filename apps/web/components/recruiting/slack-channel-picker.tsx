"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  Hash,
  Loader2,
  Lock,
  RefreshCw,
  Search,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

interface SlackChannelStatus {
  connected: boolean;
  channel_id: string | null;
  channel_name: string | null;
  accessible: boolean;
  accessibility_error: string | null;
}

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

async function fetchAuthUrl(): Promise<string> {
  const res = await fetch("/api/integrations/slack/connect");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.message || body?.detail || `Failed (${res.status})`);
  }
  const data: { auth_url: string } = await res.json();
  return data.auth_url;
}

/**
 * Picker for the org's default Slack channel (or a per-requisition override).
 * Symmetric with NotionParentPicker:
 *
 *   - `scope="org"`     — saves the choice as the org default.
 *   - `scope="publish"` — returns the choice to the caller without persisting;
 *                         used as a per-requisition override.
 */
export function SlackChannelPicker({
  open,
  onOpenChange,
  scope,
  onPicked,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  scope: "org" | "publish";
  onPicked?: (channel: SlackChannel) => void;
}) {
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [scopeUpgradeNeeded, setScopeUpgradeNeeded] = useState(false);
  // Channel id currently being auto-joined — for the spinner on the row.
  const [joiningChannelId, setJoiningChannelId] = useState<string | null>(null);

  const { data: status, mutate: mutateStatus } = useSWR<SlackChannelStatus>(
    open ? "/api/recruiting/slack-channel" : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  const channelsKey = useMemo(() => {
    if (!open || !status?.connected) return null;
    return "/api/integrations/slack/channels";
  }, [open, status?.connected]);

  const {
    data: channelsResponse,
    isLoading: loadingChannels,
    mutate: mutateChannels,
  } = useSWR<{ channels: SlackChannel[] }>(channelsKey, fetcher, {
    revalidateOnFocus: false,
  });

  const allChannels = channelsResponse?.channels ?? [];
  const channels = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allChannels;
    return allChannels.filter((c) => c.name?.toLowerCase().includes(q));
  }, [allChannels, query]);

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const refreshChannels = async () => {
    // Force a server-side refresh — Slack's channels endpoint caches for 1h.
    await fetch("/api/integrations/slack/channels?refresh=true").catch(() => {
      /* swallow — mutate below will surface the network error if it persists */
    });
    await mutateChannels();
  };

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const authUrl = await fetchAuthUrl();
      window.location.href = authUrl;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't start Slack connect.");
      setConnecting(false);
    }
  };

  const ensureBotInChannel = async (channel: SlackChannel): Promise<boolean> => {
    // Already in. Nothing to do.
    if (channel.is_member) return true;
    // Private channel: Slack has no self-join API. Fall back to the manual
    // /invite wall; this branch is only reached if the row was clicked,
    // which the disabled-state logic should already prevent.
    if (channel.is_private) {
      toast.error(
        `Private channels need a manual /invite @NirnayaIQ from inside #${channel.name}.`,
      );
      return false;
    }
    setJoiningChannelId(channel.id);
    try {
      const res = await fetch(
        `/api/integrations/slack/channels/${encodeURIComponent(channel.id)}/join`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail: string =
          body?.detail || body?.message || `Failed (${res.status})`;
        if (detail === "slack_scope_upgrade_required") {
          setScopeUpgradeNeeded(true);
          toast.error(
            "Slack needs to be re-authorised before the bot can self-join channels.",
          );
        } else if (detail === "slack_cannot_join_private") {
          toast.error(
            `Private channels need a manual /invite @NirnayaIQ from inside #${channel.name}.`,
          );
        } else if (detail === "slack_token_revoked") {
          toast.error("Slack token was revoked. Reconnect Slack.");
        } else {
          toast.error(detail);
        }
        return false;
      }
      // Optimistically update SWR so the row's "joining…" spinner can clear
      // and the badge changes to "current" if the user proceeds.
      await mutateChannels();
      return true;
    } finally {
      setJoiningChannelId(null);
    }
  };

  const handlePick = async (channel: SlackChannel) => {
    const joined = await ensureBotInChannel(channel);
    if (!joined) return;

    if (scope === "publish") {
      onPicked?.({ ...channel, is_member: true });
      onOpenChange(false);
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/recruiting/slack-channel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel_id: channel.id,
          channel_name: channel.name,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail: string =
          body?.detail || body?.message || `Failed (${res.status})`;
        if (detail === "slack_scope_upgrade_required") {
          setScopeUpgradeNeeded(true);
        }
        throw new Error(detail);
      }
      toast.success(`Requisitions will post to #${channel.name}`);
      await mutateStatus();
      await globalMutate("/api/recruiting/slack-channel");
      onPicked?.({ ...channel, is_member: true });
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save channel.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {scope === "org"
              ? "Pick a Slack channel for recruiting announcements"
              : "Post this requisition to a different channel"}
          </DialogTitle>
        </DialogHeader>

        {status && !status.connected ? (
          <div className="space-y-3 rounded-md border border-border bg-muted/40 p-4 text-sm">
            <p className="font-medium">Connect Slack to get started</p>
            <p className="text-muted-foreground">
              We&apos;ll redirect you to Slack to pick a workspace. After you
              grant access, your channels will appear here.
            </p>
            <Button onClick={handleConnect} disabled={connecting}>
              {connecting ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Redirecting…
                </>
              ) : (
                "Connect Slack"
              )}
            </Button>
          </div>
        ) : (
          <>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search channels…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="pl-8"
                />
              </div>
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={refreshChannels}
                aria-label="Refresh channel list"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </Button>
            </div>

            <div className="max-h-72 overflow-y-auto rounded border border-border">
              {loadingChannels ? (
                <div className="flex items-center justify-center p-6 text-xs text-muted-foreground">
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Loading channels…
                </div>
              ) : channels.length === 0 ? (
                <div className="space-y-3 p-4 text-sm">
                  <div className="flex items-start gap-2 text-amber">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>
                      No channels found. Invite the NirnayaIQ bot to the
                      channel you want to use, then refresh.
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    In Slack: open the channel → channel name → Integrations →
                    Add an app → NirnayaIQ. Then come back and refresh.
                  </p>
                  <Button size="sm" onClick={handleConnect} disabled={connecting}>
                    {connecting ? (
                      <>
                        <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        Redirecting…
                      </>
                    ) : (
                      "Re-authorise NirnayaIQ in Slack"
                    )}
                  </Button>
                </div>
              ) : (
                <ul className="divide-y divide-border">
                  {channels.map((c) => {
                    const isCurrent = status?.channel_id === c.id;
                    // Private channels with the bot not in them: Slack has
                    // no self-join API, so block selection. Public channels
                    // are clickable even when the bot isn't a member — the
                    // pick handler self-joins via conversations.join first.
                    const requiresManualInvite = !c.is_member && c.is_private;
                    const willAutoJoin = !c.is_member && !c.is_private;
                    const isJoining = joiningChannelId === c.id;
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          disabled={saving || isJoining || requiresManualInvite}
                          onClick={() => handlePick(c)}
                          title={
                            requiresManualInvite
                              ? `Private channel — invite the bot manually: in #${c.name}, type /invite @NirnayaIQ`
                              : willAutoJoin
                                ? "Bot will join automatically when you pick this channel."
                                : undefined
                          }
                          className={`flex w-full items-center justify-between gap-3 p-3 text-left text-sm transition disabled:cursor-not-allowed ${
                            requiresManualInvite
                              ? "opacity-60"
                              : "hover:bg-accent/50 disabled:opacity-50"
                          }`}
                        >
                          <div className="flex min-w-0 flex-1 items-center gap-2">
                            {c.is_private ? (
                              <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            ) : (
                              <Hash className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            )}
                            <span className="truncate font-medium">{c.name}</span>
                            {requiresManualInvite && (
                              <span className="rounded-full bg-amber-tint px-2 py-0.5 text-[10px] font-bold text-amber">
                                invite bot
                              </span>
                            )}
                            {willAutoJoin && !isJoining && (
                              <span className="rounded-full bg-brand-tint px-2 py-0.5 text-[10px] font-bold text-brand">
                                auto-join
                              </span>
                            )}
                            {isJoining && (
                              <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                joining…
                              </span>
                            )}
                          </div>
                          {isCurrent && (
                            <span className="inline-flex items-center gap-1 text-xs font-semibold text-success">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              current
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {scopeUpgradeNeeded && (
              <div className="rounded-xl border border-amber/30 bg-amber-tint p-3 text-xs text-amber">
                <p className="font-medium">Re-authorise Slack to enable auto-join.</p>
                <p className="mt-1">
                  Your Slack install pre-dates the{" "}
                  <code className="rounded bg-amber/15 px-1 py-0.5 font-semibold text-amber">
                    channels:join
                  </code>{" "}
                  scope. Reconnect and the bot will join public channels for
                  you on click.
                </p>
                <Button
                  size="sm"
                  className="mt-2"
                  onClick={handleConnect}
                  disabled={connecting}
                >
                  {connecting ? (
                    <>
                      <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                      Redirecting…
                    </>
                  ) : (
                    "Re-authorise Slack"
                  )}
                </Button>
              </div>
            )}

            {channels.some((c) => !c.is_member && c.is_private) && (
              <div className="rounded-xl border border-amber/30 bg-amber-tint p-3 text-xs text-amber">
                <p className="font-medium">
                  Private channels need a manual invite.
                </p>
                <p className="mt-1">
                  Public channels marked{" "}
                  <span className="rounded-full bg-brand-tint px-1.5 py-0.5 text-[10px] font-bold text-brand">
                    auto-join
                  </span>{" "}
                  add the bot for you when you click. For private channels,
                  open them in Slack and type{" "}
                  <code className="rounded bg-amber/15 px-1 py-0.5 font-semibold text-amber">
                    /invite @NirnayaIQ
                  </code>
                  , then hit refresh.
                </p>
              </div>
            )}

            <p className="text-xs text-muted-foreground">
              Don&apos;t see the channel you want?{" "}
              <button
                type="button"
                onClick={handleConnect}
                disabled={connecting}
                className="font-semibold text-brand hover:underline disabled:opacity-60"
              >
                Re-authorise Slack
              </button>
              .
            </p>
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
