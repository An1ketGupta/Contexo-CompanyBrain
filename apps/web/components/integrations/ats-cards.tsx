"use client";

/**
 * Settings-page cards for Greenhouse, Lever, and Ashby.
 *
 * Auth model is API-key, not OAuth — the recruiter pastes their provider
 * API key into a modal, we POST it to the backend which calls
 * test_connection() before storing in the unified `integrations` table.
 *
 * On success the backend also warms the mapping cache (offices/depts/teams)
 * so the first publish doesn't pay the taxonomy round-trip.
 */
import { useState } from "react";
import { toast } from "sonner";
import {
  Briefcase,
  Loader2,
  RefreshCw,
  Unplug,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type AtsProvider = "greenhouse" | "lever" | "ashby";

export interface AtsStatusBlock {
  connected: boolean;
  destination_type?: "ats" | null;
  connected_at?: string | null;
  last_error?: string | null;
  mapping_cached_at?: string | null;
  metadata_summary?: Record<string, unknown>;
}

export interface AtsStatusResponse {
  greenhouse: AtsStatusBlock;
  lever: AtsStatusBlock;
  ashby: AtsStatusBlock;
}

const PROVIDER_COPY: Record<
  AtsProvider,
  { name: string; description: string; kind: "ats" }
> = {
  greenhouse: {
    name: "Greenhouse",
    kind: "ats",
    description:
      "Publish AI-drafted JDs straight to Greenhouse Harvest API. Requires a Harvest API key with `job:write` scope.",
  },
  lever: {
    name: "Lever",
    kind: "ats",
    description:
      "Publish to Lever's Postings API. Requires a production API key (sandbox keys hit a different host).",
  },
  ashby: {
    name: "Ashby",
    kind: "ats",
    description:
      "Publish to Ashby's Public API. Requires an API key with `apiKey:read` and `jobOpening:create`.",
  },
};

function Card({
  title,
  description,
  kind,
  children,
}: {
  title: string;
  description: string;
  kind: "ats";
  children: React.ReactNode;
}) {
  const Icon = Briefcase;
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-start gap-3">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-tint text-brand">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{title}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function AtsCard({
  provider,
  status,
  onChanged,
}: {
  provider: AtsProvider;
  status: AtsStatusBlock | undefined;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const copy = PROVIDER_COPY[provider];

  if (!status?.connected) {
    return (
      <Card title={copy.name} description={copy.description} kind={copy.kind}>
        <Button size="sm" onClick={() => setOpen(true)}>
          Connect {copy.name}
        </Button>
        <ConnectDialog
          provider={provider}
          open={open}
          onOpenChange={setOpen}
          onConnected={onChanged}
        />
      </Card>
    );
  }

  return (
    <Card
      title={copy.name}
      kind={copy.kind}
      description={
        status.last_error
          ? `Last error: ${status.last_error}`
          : status.mapping_cached_at
            ? `Mapping cached ${new Date(status.mapping_cached_at).toLocaleString()}`
            : "Connected"
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={async () => {
            setRefreshing(true);
            try {
              const res = await fetch(
                `/api/integrations/ats/${provider}/refresh-mapping`,
                { method: "POST" },
              );
              if (!res.ok) {
                toast.error("Refresh failed");
                return;
              }
              toast.success("Mapping cache refreshed");
              onChanged();
            } finally {
              setRefreshing(false);
            }
          }}
        >
          {refreshing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}{" "}
          Refresh mapping
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (!confirm(`Disconnect ${copy.name}? Future publishes will fail until you reconnect.`))
              return;
            const res = await fetch(`/api/integrations/ats/${provider}`, {
              method: "DELETE",
            });
            if (!res.ok) {
              toast.error("Disconnect failed");
              return;
            }
            toast.success("Disconnected");
            onChanged();
          }}
        >
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
      </div>
    </Card>
  );
}

function ConnectDialog({
  provider,
  open,
  onOpenChange,
  onConnected,
}: {
  provider: AtsProvider;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onConnected: () => void;
}) {
  const copy = PROVIDER_COPY[provider];
  const [apiKey, setApiKey] = useState("");
  const [boardSubdomain, setBoardSubdomain] = useState("");
  const [onBehalfOf, setOnBehalfOf] = useState("");
  const [postingOwner, setPostingOwner] = useState("");
  const [hiringTeamMember, setHiringTeamMember] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!apiKey.trim()) {
      toast.error("API key required");
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, string> = { api_key: apiKey.trim() };
      if (provider === "greenhouse") {
        if (boardSubdomain.trim()) body.board_subdomain = boardSubdomain.trim();
        if (onBehalfOf.trim()) body.on_behalf_of_user_id = onBehalfOf.trim();
      } else if (provider === "lever") {
        if (postingOwner.trim()) body.posting_owner_user_id = postingOwner.trim();
      } else if (provider === "ashby") {
        if (hiringTeamMember.trim())
          body.hiring_team_member_id = hiringTeamMember.trim();
      }

      const res = await fetch(`/api/integrations/ats/${provider}/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast.error(err.message ?? err.detail ?? "Connect failed");
        return;
      }
      toast.success(`${copy.name} connected`);
      onConnected();
      onOpenChange(false);
      setApiKey("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Connect {copy.name}</DialogTitle>
          <DialogDescription>{copy.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="api-key">API key</Label>
            <Input
              id="api-key"
              type="password"
              autoComplete="off"
              placeholder="Paste your API key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <p className="text-[11px] text-muted-foreground">
              Stored encrypted at rest. Used server-side only.
            </p>
          </div>

          {provider === "greenhouse" && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="board-subdomain">Board subdomain (optional)</Label>
                <Input
                  id="board-subdomain"
                  placeholder="acme"
                  value={boardSubdomain}
                  onChange={(e) => setBoardSubdomain(e.target.value)}
                />
                <p className="text-[11px] text-muted-foreground">
                  Used to build the post-publish link
                  (https://&lt;subdomain&gt;.greenhouse.io).
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="on-behalf">On-behalf-of user ID (optional)</Label>
                <Input
                  id="on-behalf"
                  placeholder="Greenhouse user_id"
                  value={onBehalfOf}
                  onChange={(e) => setOnBehalfOf(e.target.value)}
                />
              </div>
            </>
          )}
          {provider === "lever" && (
            <div className="space-y-1.5">
              <Label htmlFor="posting-owner">Posting owner user ID (optional)</Label>
              <Input
                id="posting-owner"
                placeholder="Lever user_id"
                value={postingOwner}
                onChange={(e) => setPostingOwner(e.target.value)}
              />
            </div>
          )}
          {provider === "ashby" && (
            <div className="space-y-1.5">
              <Label htmlFor="hiring-team">Hiring team member ID (optional)</Label>
              <Input
                id="hiring-team"
                placeholder="Ashby user_id"
                value={hiringTeamMember}
                onChange={(e) => setHiringTeamMember(e.target.value)}
              />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button size="sm" onClick={submit} disabled={saving || !apiKey.trim()}>
            {saving ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Validating…
              </>
            ) : (
              "Connect"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
