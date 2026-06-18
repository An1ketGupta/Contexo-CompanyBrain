"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  Copy,
  Folder,
  FolderPlus,
  Loader2,
  Mail,
  MessageSquare,
  RefreshCw,
  Search,
  Settings2,
  Unplug,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { openDriveFolderPicker } from "@/components/integrations/google-drive-picker";
import {
  ConfluenceCard,
  type ConfluenceStatus,
  DropboxCard,
  type DropboxStatus,
  GitHubCard,
  type GitHubStatus,
  OneDriveCard,
  type OneDriveStatus,
} from "@/components/integrations/v2-cards";

interface DriveStatus {
  available: boolean;
  connected: boolean;
  folder_ids: string[];
  last_synced_at: string | null;
}
interface NotionStatus {
  available: boolean;
  connected: boolean;
  workspace_name: string | null;
  selected_pages: { id: string; title: string }[];
  last_synced_at: string | null;
}
interface EmailStatus {
  available: boolean;
  address: string | null;
}
interface SlackSubscription {
  channel_id: string;
  channel_name: string | null;
  is_private: boolean;
  last_backfilled_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
}
interface SlackStatus {
  available: boolean;
  connected: boolean;
  workspace_name: string | null;
  installed_at: string | null;
  scopes_complete: boolean;
  has_canvas_scope: boolean;
  required_scopes: string[];
  subscriptions: SlackSubscription[];
}
interface GmailStatus {
  available: boolean;
  connected: boolean;
  has_send_scope: boolean;
  email_address: string | null;
  connected_at: string | null;
  last_used_at: string | null;
}
interface StatusResponse {
  drive: DriveStatus;
  notion: NotionStatus;
  email: EmailStatus;
  slack?: SlackStatus;
  gmail?: GmailStatus;
  onedrive?: OneDriveStatus;
  confluence?: ConfluenceStatus;
  github?: GitHubStatus;
  dropbox?: DropboxStatus;
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function IntegrationsPage() {
  const search = useSearchParams();
  const { data, mutate, isLoading } = useSWR<StatusResponse>(
    "/api/integrations/status",
    fetcher,
  );

  useEffect(() => {
    const connected = search.get("connected");
    const err = search.get("error");
    if (connected) toast.success(`${connected} connected`);
    if (err) toast.error(`Connection failed: ${err}`);
  }, [search]);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to settings
      </Link>

      <header>
        <h1 className="text-xl font-semibold tracking-tight">Integrations</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Pipe in documents from elsewhere and let your team use the brain
          inside the tools they already live in.
        </p>
      </header>

      {isLoading || !data ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : (
        <div className="space-y-4">
          <DriveCard status={data.drive} onChanged={mutate} />
          <GmailCard status={data.gmail} onChanged={mutate} />
          <NotionCard status={data.notion} onChanged={mutate} />
          <OneDriveCard status={data.onedrive} onChanged={mutate} />
          <ConfluenceCard status={data.confluence} onChanged={mutate} />
          <GitHubCard status={data.github} onChanged={mutate} />
          <DropboxCard status={data.dropbox} onChanged={mutate} />
          <EmailCard status={data.email} onChanged={mutate} />
          <SlackCard status={data.slack} onChanged={mutate} />
        </div>
      )}
    </div>
  );
}

function Card({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border bg-background p-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-muted">
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function DriveCard({
  status,
  onChanged,
}: {
  status: DriveStatus;
  onChanged: () => void;
}) {
  if (!status.available) {
    return (
      <Card
        icon={<Folder className="h-4 w-4" />}
        title="Google Drive"
        description="Not configured. Ask your operator to set GOOGLE_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<Folder className="h-4 w-4" />}
        title="Google Drive"
        description="Auto-sync documents from specific Drive folders every 5 minutes."
      >
        <Button
          size="sm"
          onClick={async () => {
            const res = await fetch("/api/integrations/drive/connect");
            if (!res.ok) return toast.error("Could not start OAuth");
            const { auth_url } = await res.json();
            window.location.href = auth_url;
          }}
        >
          Connect Drive
        </Button>
      </Card>
    );
  }

  return (
    <Card
      icon={<Folder className="h-4 w-4" />}
      title="Google Drive"
      description={
        status.last_synced_at
          ? `Last synced ${new Date(status.last_synced_at).toLocaleString()}`
          : "Not synced yet"
      }
    >
      <DriveFolderManager status={status} onChanged={onChanged} />
    </Card>
  );
}

function DriveFolderManager({
  status,
  onChanged,
}: {
  status: DriveStatus;
  onChanged: () => void;
}) {
  const [syncing, setSyncing] = useState(false);
  const [opening, setOpening] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});

  const folderIds = status.folder_ids;
  const folderIdsKey = folderIds.join(",");

  useEffect(() => {
    if (!folderIdsKey) {
      setNames({});
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/integrations/drive/folders/names?ids=${encodeURIComponent(folderIdsKey)}`,
        );
        if (!res.ok) return;
        const json = await res.json();
        if (!cancelled) setNames(json.names || {});
      } catch {
        // Silent — falls back to the raw ID, which is acceptable.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [folderIdsKey]);

  const removeChip = async (id: string) => {
    const res = await fetch(
      `/api/integrations/drive/folders/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
    if (!res.ok) return toast.error("Remove failed");
    onChanged();
  };

  const launchPicker = async () => {
    const developerKey = process.env.NEXT_PUBLIC_GOOGLE_API_KEY;
    const appId = process.env.NEXT_PUBLIC_GOOGLE_APP_ID;
    if (!developerKey) {
      toast.error(
        "Picker unavailable — NEXT_PUBLIC_GOOGLE_API_KEY is not configured",
      );
      return;
    }
    setOpening(true);
    try {
      const tokenRes = await fetch("/api/integrations/drive/picker-token");
      if (!tokenRes.ok) {
        toast.error("Could not authorize the picker — try reconnecting Drive");
        return;
      }
      const { access_token } = (await tokenRes.json()) as {
        access_token: string;
      };

      const picked = await openDriveFolderPicker({
        accessToken: access_token,
        developerKey,
        appId,
        alreadySelectedIds: folderIds,
      });
      if (picked.length === 0) return; // user cancelled

      // Dedupe — Drive Picker happily returns the same ID twice if you click
      // a folder that's already configured.
      const existing = new Set(folderIds);
      const toAdd = picked.filter((f) => !existing.has(f.id));
      if (toAdd.length === 0) {
        toast.info("Those folders are already connected.");
        return;
      }

      let added = 0;
      for (const folder of toAdd) {
        const res = await fetch("/api/integrations/drive/folders", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            folder_id: folder.id,
            folder_name: folder.name,
          }),
        });
        if (!res.ok) {
          toast.error(`Add failed for "${folder.name}"`);
          continue;
        }
        added += 1;
      }
      if (added > 0) {
        toast.success(`Added ${added} folder${added === 1 ? "" : "s"}`);
        onChanged();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Picker failed to open";
      toast.error(msg);
    } finally {
      setOpening(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1.5 text-xs font-medium text-muted-foreground">
          Connected folders ({folderIds.length})
        </p>
        {folderIds.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No folders connected yet. Click <span className="font-medium">Add folders</span> to pick from Drive.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {folderIds.map((id) => {
              const label = names[id] ?? id;
              return (
                <span
                  key={id}
                  className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 py-0.5 pl-2 pr-1 text-xs"
                  title={id}
                >
                  <Folder className="h-3 w-3 text-muted-foreground" />
                  <span className="max-w-[200px] truncate">{label}</span>
                  <button
                    type="button"
                    onClick={() => removeChip(id)}
                    className="rounded-full p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                    aria-label={`Remove ${label}`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button size="sm" onClick={launchPicker} disabled={opening}>
          {opening ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Opening…
            </>
          ) : (
            <>
              <FolderPlus className="h-3.5 w-3.5" /> Add folders
            </>
          )}
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={syncing}
          onClick={async () => {
            setSyncing(true);
            try {
              const res = await fetch("/api/integrations/drive/sync", {
                method: "POST",
              });
              if (!res.ok) return toast.error("Sync failed");
              toast.success("Sync triggered");
              onChanged();
            } finally {
              setSyncing(false);
            }
          }}
        >
          <RefreshCw className="h-3.5 w-3.5" /> Sync now
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (!confirm("Disconnect Google Drive?")) return;
            const res = await fetch("/api/integrations/drive", {
              method: "DELETE",
            });
            if (!res.ok) return toast.error("Disconnect failed");
            toast.success("Disconnected");
            onChanged();
          }}
        >
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
      </div>
    </div>
  );
}

function NotionCard({
  status,
  onChanged,
}: {
  status: NotionStatus;
  onChanged: () => void;
}) {
  if (!status.available) {
    return (
      <Card
        icon={<span className="text-base">N</span>}
        title="Notion"
        description="Not configured. Ask your operator to set NOTION_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<span className="text-base">N</span>}
        title="Notion"
        description="Sync specific Notion pages into your knowledge base every 10 minutes."
      >
        <Button
          size="sm"
          onClick={async () => {
            const res = await fetch("/api/integrations/notion/connect");
            if (!res.ok) return toast.error("Could not start OAuth");
            const { auth_url } = await res.json();
            window.location.href = auth_url;
          }}
        >
          Connect Notion
        </Button>
      </Card>
    );
  }

  return (
    <Card
      icon={<span className="text-base">N</span>}
      title={`Notion · ${status.workspace_name ?? "Workspace"}`}
      description={
        status.last_synced_at
          ? `Last synced ${new Date(status.last_synced_at).toLocaleString()}`
          : "Not synced yet"
      }
    >
      <NotionPageManager
        selected={status.selected_pages}
        onChanged={onChanged}
      />
    </Card>
  );
}

function NotionPageManager({
  selected,
  onChanged,
}: {
  selected: { id: string; title: string }[];
  onChanged: () => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const removeChip = async (id: string) => {
    const next = selected.filter((p) => p.id !== id);
    const res = await fetch("/api/integrations/notion/pages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pages: next }),
    });
    if (!res.ok) return toast.error("Remove failed");
    onChanged();
  };

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1.5 text-xs font-medium text-muted-foreground">
          Connected pages ({selected.length})
        </p>
        {selected.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No pages connected yet. Click <span className="font-medium">Manage pages</span> to pick.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {selected.map((p) => (
              <span
                key={p.id}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 py-0.5 pl-2 pr-1 text-xs"
                title={p.id}
              >
                <span className="text-muted-foreground">N</span>
                <span className="max-w-[200px] truncate">{p.title}</span>
                <button
                  type="button"
                  onClick={() => removeChip(p.id)}
                  className="rounded-full p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                  aria-label={`Remove ${p.title}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button size="sm" variant="outline" onClick={() => setPickerOpen(true)}>
          <Settings2 className="h-3.5 w-3.5" />
          Manage pages
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={syncing}
          onClick={async () => {
            setSyncing(true);
            try {
              const res = await fetch("/api/integrations/notion/sync", {
                method: "POST",
              });
              if (!res.ok) return toast.error("Sync failed");
              toast.success("Sync triggered");
              onChanged();
            } finally {
              setSyncing(false);
            }
          }}
        >
          <RefreshCw className="h-3.5 w-3.5" /> Sync now
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (!confirm("Disconnect Notion?")) return;
            const res = await fetch("/api/integrations/notion", {
              method: "DELETE",
            });
            if (!res.ok) return toast.error("Disconnect failed");
            toast.success("Disconnected");
            onChanged();
          }}
        >
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
      </div>

      <NotionPagePickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        initialSelected={selected}
        onSaved={onChanged}
      />
    </div>
  );
}

function NotionPagePickerDialog({
  open,
  onOpenChange,
  initialSelected,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialSelected: { id: string; title: string }[];
  onSaved: () => void;
}) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [results, setResults] = useState<
    { id: string; title: string; url?: string }[]
  >([]);
  const [chosen, setChosen] = useState<Map<string, string>>(
    new Map(initialSelected.map((p) => [p.id, p.title])),
  );

  useEffect(() => {
    if (open) {
      setChosen(new Map(initialSelected.map((p) => [p.id, p.title])));
      setQuery("");
    }
  }, [open, initialSelected]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/integrations/notion/pages?q=${encodeURIComponent(query.trim())}`,
        );
        if (!res.ok) {
          if (!cancelled) toast.error("Page list failed");
          return;
        }
        const json = await res.json();
        if (!cancelled) setResults(json.pages || []);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, query ? 250 : 0);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [open, query]);

  const toggle = (p: { id: string; title: string }) =>
    setChosen((prev) => {
      const next = new Map(prev);
      if (next.has(p.id)) next.delete(p.id);
      else next.set(p.id, p.title);
      return next;
    });

  const save = async () => {
    setSaving(true);
    try {
      const pages = [...chosen.entries()].map(([id, title]) => ({ id, title }));
      const res = await fetch("/api/integrations/notion/pages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pages }),
      });
      if (!res.ok) {
        toast.error("Save failed");
        return;
      }
      toast.success("Pages saved");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  const visibleIds = new Set(results.map((r) => r.id));
  const orphanSelected = [...chosen.entries()].filter(
    ([id]) => !visibleIds.has(id),
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Pick Notion pages</DialogTitle>
          <DialogDescription>
            Selected pages are polled every 10 minutes. Pages must be shared with
            the NirnayaIQ integration in Notion to appear here.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-7"
              placeholder="Filter pages by title…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          </div>

          <div className="max-h-[55vh] overflow-auto rounded-md border border-border">
            {loading && results.length === 0 ? (
              <div className="flex items-center gap-2 px-3 py-6 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading pages…
              </div>
            ) : results.length === 0 && orphanSelected.length === 0 ? (
              <p className="px-3 py-6 text-xs text-muted-foreground">
                {query
                  ? "No pages match that title."
                  : "No pages visible — share a page with the NirnayaIQ integration in Notion to see it here."}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {orphanSelected.map(([id, title]) => (
                  <li
                    key={id}
                    className="flex items-center gap-2 px-3 py-2 text-xs"
                  >
                    <Checkbox
                      checked
                      onCheckedChange={() => toggle({ id, title })}
                      aria-label={`Deselect ${title}`}
                    />
                    <span className="text-muted-foreground">N</span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{title}</p>
                      <p className="text-[10px] text-muted-foreground">
                        Selected · not in current filter
                      </p>
                    </div>
                  </li>
                ))}
                {results.map((p) => {
                  const isOn = chosen.has(p.id);
                  return (
                    <li
                      key={p.id}
                      className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs hover:bg-muted/50"
                      onClick={() => toggle({ id: p.id, title: p.title })}
                    >
                      <Checkbox
                        checked={isOn}
                        onCheckedChange={() =>
                          toggle({ id: p.id, title: p.title })
                        }
                        aria-label={p.title}
                      />
                      <span className="text-muted-foreground">N</span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">{p.title}</p>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <p className="text-xs text-muted-foreground">
            {chosen.size} page{chosen.size === 1 ? "" : "s"} selected
          </p>
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
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…
              </>
            ) : (
              "Save selection"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EmailCard({
  status,
  onChanged,
}: {
  status: EmailStatus;
  onChanged: () => void;
}) {
  const [copied, setCopied] = useState(false);

  if (!status.available) {
    return (
      <Card
        icon={<span className="text-base">@</span>}
        title="Email forward-to-brain"
        description="Not configured. Ask your operator to set INBOUND_EMAIL_DOMAIN."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  return (
    <Card
      icon={<span className="text-base">@</span>}
      title="Email forward-to-brain"
      description="Forward any email thread to this address and it lands in your knowledge base."
    >
      {status.address ? (
        <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2">
          <code className="flex-1 truncate text-xs">{status.address}</code>
          <Button
            size="sm"
            variant="ghost"
            onClick={async () => {
              await navigator.clipboard.writeText(status.address!);
              setCopied(true);
              setTimeout(() => setCopied(false), 1_500);
            }}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </Button>
        </div>
      ) : (
        <Button
          size="sm"
          onClick={async () => {
            const res = await fetch("/api/integrations/email/address", {
              method: "POST",
            });
            if (!res.ok) return toast.error("Could not provision");
            onChanged();
          }}
        >
          Generate inbound address
        </Button>
      )}
    </Card>
  );
}

function GmailCard({
  status,
  onChanged,
}: {
  status?: GmailStatus;
  onChanged: () => void;
}) {
  const startConnect = async () => {
    const res = await fetch("/api/integrations/gmail/connect");
    if (!res.ok) return toast.error("Could not start Gmail OAuth");
    const { auth_url } = await res.json();
    window.location.href = auth_url;
  };

  if (!status || !status.available) {
    return (
      <Card
        icon={<Mail className="h-4 w-4" />}
        title="Gmail (send)"
        description="Not configured. Ask your operator to set GOOGLE_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<Mail className="h-4 w-4" />}
        title="Gmail (send)"
        description="Send AI-drafted emails directly from your own Gmail address. Connect is per-user — each teammate connects their own mailbox."
      >
        <Button size="sm" onClick={startConnect}>Connect Gmail</Button>
      </Card>
    );
  }

  if (!status.has_send_scope) {
    return (
      <Card
        icon={<Mail className="h-4 w-4" />}
        title={`Gmail · ${status.email_address ?? ""}`}
        description="Send permission was not granted. Reconnect to enable Send via Gmail."
      >
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={startConnect}>Reconnect Gmail</Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              if (!confirm("Disconnect Gmail?")) return;
              const res = await fetch("/api/integrations/gmail", { method: "DELETE" });
              if (!res.ok) return toast.error("Disconnect failed");
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

  return (
    <Card
      icon={<Mail className="h-4 w-4" />}
      title={`Gmail · ${status.email_address ?? ""}`}
      description={
        status.last_used_at
          ? `Last send ${new Date(status.last_used_at).toLocaleString()}`
          : status.connected_at
            ? `Connected ${new Date(status.connected_at).toLocaleString()}`
            : "Connected"
      }
    >
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (!confirm("Disconnect Gmail?")) return;
            const res = await fetch("/api/integrations/gmail", { method: "DELETE" });
            if (!res.ok) return toast.error("Disconnect failed");
            toast.success("Disconnected");
            onChanged();
          }}
        >
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        On any email-style assistant reply, click the Gmail icon in the action row to send.
      </p>
    </Card>
  );
}

function SlackCard({
  status,
  onChanged,
}: {
  status?: SlackStatus;
  onChanged: () => void;
}) {
  if (!status || !status.available) {
    return (
      <Card
        icon={<MessageSquare className="h-4 w-4" />}
        title="Slack"
        description="Not configured. Ask your operator to set SLACK_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<MessageSquare className="h-4 w-4" />}
        title="Slack"
        description="Run /brain from any channel to query your knowledge base."
      >
        <Button
          size="sm"
          onClick={async () => {
            const res = await fetch("/api/integrations/slack/connect");
            if (!res.ok) return toast.error("Could not start OAuth");
            const { auth_url } = await res.json();
            window.location.href = auth_url;
          }}
        >
          Add to Slack
        </Button>
        <p className="mt-2 text-xs text-muted-foreground">
          After installing, try{" "}
          <code className="rounded bg-muted px-1">/brain what is our refund policy?</code>{" "}
          in any channel.
        </p>
      </Card>
    );
  }

  return (
    <Card
      icon={<MessageSquare className="h-4 w-4" />}
      title={`Slack · ${status.workspace_name ?? "Workspace"}`}
      description={
        status.installed_at
          ? `Installed ${new Date(status.installed_at).toLocaleString()}`
          : "Connected"
      }
    >
      <SlackInboundManager status={status} onChanged={onChanged} />
    </Card>
  );
}

function SlackInboundManager({
  status,
  onChanged,
}: {
  status: SlackStatus;
  onChanged: () => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [resyncing, setResyncing] = useState<string | null>(null);
  const subs = status.subscriptions || [];

  const removeChannel = async (channelId: string, name: string | null) => {
    if (!confirm(`Stop ingesting from ${name ? `#${name}` : channelId}?`))
      return;
    const res = await fetch(
      `/api/integrations/slack/subscriptions/${encodeURIComponent(channelId)}`,
      { method: "DELETE" },
    );
    if (!res.ok) return toast.error("Remove failed");
    toast.success("Channel removed");
    onChanged();
  };

  const resync = async (channelId: string) => {
    setResyncing(channelId);
    try {
      const res = await fetch(
        `/api/integrations/slack/subscriptions/${encodeURIComponent(channelId)}/resync`,
        { method: "POST" },
      );
      if (!res.ok) return toast.error("Resync failed");
      toast.success("Backfill queued");
      onChanged();
    } finally {
      setResyncing(null);
    }
  };

  return (
    <div className="space-y-3">
      {!status.scopes_complete && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs dark:border-amber-700/40 dark:bg-amber-950/30">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            Reconnect Slack to enable inbound ingest
          </p>
          <p className="mt-0.5 text-amber-800 dark:text-amber-300">
            Your current install only has the scopes needed to post messages.
            To pull pins, canvases, and files into the knowledge base we need
            read permissions.
          </p>
          <Button
            size="sm"
            className="mt-2"
            onClick={async () => {
              const res = await fetch("/api/integrations/slack/connect");
              if (!res.ok) return toast.error("Could not start OAuth");
              const { auth_url } = await res.json();
              window.location.href = auth_url;
            }}
          >
            Reconnect Slack
          </Button>
        </div>
      )}

      {status.scopes_complete && !status.has_canvas_scope && (
        <p className="text-xs text-muted-foreground">
          Canvas ingest unavailable on this workspace plan — pins and files
          will still sync.
        </p>
      )}

      <div>
        <p className="mb-1.5 text-xs font-medium text-muted-foreground">
          Connected channels ({subs.length})
        </p>
        {subs.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No channels connected. Click <span className="font-medium">Add channel</span> to pick from channels the bot is in.
            The bot must be invited to a channel before it can be added.
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {subs.map((s) => (
              <li
                key={s.channel_id}
                className="flex items-center gap-2 px-3 py-2 text-xs"
              >
                <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">
                    {s.channel_name ? `#${s.channel_name}` : s.channel_id}
                    {s.is_private ? (
                      <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        private
                      </span>
                    ) : null}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {s.last_error
                      ? `⚠ ${s.last_error}`
                      : s.last_backfilled_at
                        ? `Last synced ${new Date(s.last_backfilled_at).toLocaleString()}`
                        : "Backfilling…"}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={resyncing === s.channel_id}
                  onClick={() => resync(s.channel_id)}
                  aria-label="Resync"
                  title="Resync"
                >
                  {resyncing === s.channel_id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => removeChannel(s.channel_id, s.channel_name)}
                  aria-label="Remove channel"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          size="sm"
          onClick={() => setPickerOpen(true)}
          disabled={!status.scopes_complete}
          title={
            status.scopes_complete
              ? undefined
              : "Reconnect Slack first to enable inbound"
          }
        >
          <FolderPlus className="h-3.5 w-3.5" /> Add channel
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (!confirm("Disconnect Slack? Channel subscriptions will be removed."))
              return;
            const res = await fetch("/api/integrations/slack", {
              method: "DELETE",
            });
            if (!res.ok) return toast.error("Disconnect failed");
            toast.success("Disconnected");
            onChanged();
          }}
        >
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
      </div>

      <p className="text-[11px] text-muted-foreground">
        Bot also responds to <code className="rounded bg-muted px-1">/brain &lt;question&gt;</code> in any channel.
      </p>

      <SlackChannelPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        existingChannelIds={new Set(subs.map((s) => s.channel_id))}
        onSaved={onChanged}
      />
    </div>
  );
}

interface SlackBotChannel {
  id: string;
  name: string;
  is_private: boolean;
  topic: string | null;
}

function SlackChannelPickerDialog({
  open,
  onOpenChange,
  existingChannelIds,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  existingChannelIds: Set<string>;
  onSaved: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [channels, setChannels] = useState<SlackBotChannel[]>([]);
  const [chosen, setChosen] = useState<Map<string, SlackBotChannel>>(new Map());
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) return;
    setChosen(new Map());
    setQuery("");
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await fetch("/api/integrations/slack/inbound-channels");
        if (!res.ok) {
          if (!cancelled) toast.error("Could not load channels");
          return;
        }
        const json = await res.json();
        if (!cancelled) setChannels(json.channels || []);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const filtered = channels.filter((c) => {
    if (!query.trim()) return true;
    const q = query.trim().toLowerCase();
    return c.name.toLowerCase().includes(q);
  });

  const toggle = (c: SlackBotChannel) =>
    setChosen((prev) => {
      const next = new Map(prev);
      if (next.has(c.id)) next.delete(c.id);
      else next.set(c.id, c);
      return next;
    });

  const save = async () => {
    if (chosen.size === 0) {
      onOpenChange(false);
      return;
    }
    setSaving(true);
    try {
      let added = 0;
      for (const c of chosen.values()) {
        const res = await fetch("/api/integrations/slack/subscriptions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            channel_id: c.id,
            channel_name: c.name,
            is_private: c.is_private,
          }),
        });
        if (!res.ok) {
          const detail = await res.text();
          toast.error(`Add failed for #${c.name}: ${detail}`);
          continue;
        }
        added += 1;
      }
      if (added > 0) {
        toast.success(`Added ${added} channel${added === 1 ? "" : "s"}`);
        onSaved();
        onOpenChange(false);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Slack channels</DialogTitle>
          <DialogDescription>
            Channels the bot has been invited to. Pins, canvases, and files
            from selected channels will be ingested into the knowledge base.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-7"
              placeholder="Filter channels…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          </div>

          <div className="max-h-[55vh] overflow-auto rounded-md border border-border">
            {loading ? (
              <div className="flex items-center gap-2 px-3 py-6 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading channels…
              </div>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-6 text-xs text-muted-foreground">
                {channels.length === 0
                  ? "Bot isn't in any channels yet — invite it with /invite @YourBot in a channel, then come back."
                  : "No channels match that filter."}
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {filtered.map((c) => {
                  const already = existingChannelIds.has(c.id);
                  const picked = chosen.has(c.id);
                  return (
                    <li
                      key={c.id}
                      className={
                        "flex items-center gap-2 px-3 py-2 text-xs " +
                        (already
                          ? "opacity-50"
                          : "cursor-pointer hover:bg-muted/50")
                      }
                      onClick={() => !already && toggle(c)}
                    >
                      <Checkbox
                        checked={already || picked}
                        disabled={already}
                        onCheckedChange={() => !already && toggle(c)}
                        aria-label={c.name}
                      />
                      <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">
                          #{c.name}
                          {c.is_private ? (
                            <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                              private
                            </span>
                          ) : null}
                          {already ? (
                            <span className="ml-2 text-[10px] text-muted-foreground">
                              already added
                            </span>
                          ) : null}
                        </p>
                        {c.topic ? (
                          <p className="truncate text-[10px] text-muted-foreground">
                            {c.topic}
                          </p>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <p className="text-xs text-muted-foreground">
            {chosen.size} channel{chosen.size === 1 ? "" : "s"} selected
          </p>
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
          <Button size="sm" onClick={save} disabled={saving || chosen.size === 0}>
            {saving ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…
              </>
            ) : (
              "Add channels"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
