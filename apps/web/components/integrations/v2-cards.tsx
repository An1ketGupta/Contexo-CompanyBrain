"use client";

/**
 * Settings-page cards for the post-Slack integrations wave:
 * OneDrive/SharePoint, Confluence, GitHub, Dropbox.
 *
 * All four ride the unified backend (one integrations row per (org, provider),
 * see migration 036), so the cards intentionally share a single shape:
 *   - "not configured" branch when the env var pair isn't set
 *   - "connect" branch when the row doesn't exist
 *   - "connected" branch with resource picker + sync + disconnect
 *
 * Keeping them in one file makes diffing the four cards trivial — they're 95%
 * the same surface and we want that to stay visually obvious.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Cloud,
  FileText,
  HardDrive,
  Loader2,
  RefreshCw,
  Settings2,
  Unplug,
  Video,
  X,
} from "lucide-react";

function Github({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.88-1.54-3.88-1.54-.52-1.33-1.28-1.69-1.28-1.69-1.05-.71.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.71 1.26 3.37.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.84 1.19 3.1 0 4.42-2.69 5.39-5.26 5.68.41.35.78 1.05.78 2.12 0 1.53-.01 2.77-.01 3.14 0 .31.21.67.8.56A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5z" />
    </svg>
  );
}
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

interface V2BaseStatus {
  available: boolean;
  connected: boolean;
  metadata?: Record<string, unknown>;
  resources?: unknown[];
  last_synced_at?: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
  scopes?: string[];
}

export interface OneDriveStatus extends V2BaseStatus {}
export interface ConfluenceStatus extends V2BaseStatus {}
export interface GitHubStatus extends V2BaseStatus {}
export interface DropboxStatus extends V2BaseStatus {}
export interface ZoomStatus extends V2BaseStatus {}

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
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-start gap-3">
        <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-tint text-brand">
          {icon}
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

async function startConnect(path: string) {
  const res = await fetch(path);
  if (!res.ok) {
    toast.error("Could not start OAuth");
    return;
  }
  const { auth_url } = (await res.json()) as { auth_url: string };
  window.location.href = auth_url;
}

function StatusFooter({
  status,
  onSync,
  onDisconnect,
  syncing,
}: {
  status: V2BaseStatus;
  onSync: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  syncing: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <Button variant="outline" size="sm" disabled={syncing} onClick={onSync}>
        {syncing ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <RefreshCw className="h-3.5 w-3.5" />
        )}{" "}
        Sync now
      </Button>
      <Button variant="ghost" size="sm" onClick={onDisconnect}>
        <Unplug className="h-3.5 w-3.5" /> Disconnect
      </Button>
      {status.last_error ? (
        <span
          className="ml-1 truncate text-[11px] text-amber-ink"
          title={status.last_error}
        >
          ⚠ {status.last_error}
        </span>
      ) : null}
    </div>
  );
}

async function genericSync(path: string): Promise<boolean> {
  const res = await fetch(path, { method: "POST" });
  if (!res.ok) {
    toast.error("Sync failed");
    return false;
  }
  toast.success("Sync triggered");
  return true;
}

async function genericDisconnect(path: string, label: string): Promise<boolean> {
  if (!confirm(`Disconnect ${label}?`)) return false;
  const res = await fetch(path, { method: "DELETE" });
  if (!res.ok) {
    toast.error("Disconnect failed");
    return false;
  }
  toast.success("Disconnected");
  return true;
}

// ──────────────────────────────────────────────────────────────────────────
// OneDrive / SharePoint
// ──────────────────────────────────────────────────────────────────────────

interface OneDriveResource {
  kind: "drive" | "site";
  drive_id: string;
  site_id?: string | null;
  name: string;
}

export function OneDriveCard({
  status,
  onChanged,
}: {
  status?: OneDriveStatus;
  onChanged: () => void;
}) {
  const [syncing, setSyncing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  if (!status || !status.available) {
    return (
      <Card
        icon={<Cloud className="h-4 w-4" />}
        title="OneDrive & SharePoint"
        description="Not configured. Ask your operator to set MICROSOFT_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<Cloud className="h-4 w-4" />}
        title="OneDrive & SharePoint"
        description="Sync docs from Microsoft 365 — OneDrive Business folders and SharePoint document libraries. Polls every 10 minutes."
      >
        <Button
          size="sm"
          onClick={() => startConnect("/api/integrations/onedrive/connect")}
        >
          Connect Microsoft
        </Button>
        <p className="mt-2 text-xs text-muted-foreground">
          Admin consent required. After installing, pick which drives and
          SharePoint sites to ingest.
        </p>
      </Card>
    );
  }

  const account =
    (status.metadata as { account?: { display_name?: string; user_principal_name?: string } })
      ?.account || {};
  const resources = (status.resources || []) as OneDriveResource[];

  return (
    <Card
      icon={<Cloud className="h-4 w-4" />}
      title={`OneDrive · ${account.display_name || account.user_principal_name || "Connected"}`}
      description={
        status.last_synced_at
          ? `Last synced ${new Date(status.last_synced_at).toLocaleString()}`
          : "Not synced yet"
      }
    >
      <div className="space-y-3">
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Connected drives + sites ({resources.length})
          </p>
          {resources.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No drives selected yet. Click{" "}
              <span className="font-medium">Manage sources</span> to pick.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {resources.map((r) => (
                <span
                  key={`${r.kind}-${r.drive_id}`}
                  className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 py-0.5 pl-2 pr-2 text-xs"
                  title={r.drive_id}
                >
                  <HardDrive className="h-3 w-3 text-muted-foreground" />
                  <span className="max-w-[200px] truncate">{r.name}</span>
                  <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">
                    {r.kind === "site" ? "SharePoint" : "OneDrive"}
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setPickerOpen(true)}>
            <Settings2 className="h-3.5 w-3.5" /> Manage sources
          </Button>
        </div>
        <StatusFooter
          status={status}
          syncing={syncing}
          onSync={async () => {
            setSyncing(true);
            try {
              if (await genericSync("/api/integrations/onedrive/sync")) onChanged();
            } finally {
              setSyncing(false);
            }
          }}
          onDisconnect={async () => {
            if (await genericDisconnect("/api/integrations/onedrive", "Microsoft"))
              onChanged();
          }}
        />
      </div>
      <OneDrivePicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        selected={resources}
        onSaved={onChanged}
      />
    </Card>
  );
}

function OneDrivePicker({
  open,
  onOpenChange,
  selected,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  selected: OneDriveResource[];
  onSaved: () => void;
}) {
  const [tab, setTab] = useState<"drives" | "sites">("drives");
  const [siteQuery, setSiteQuery] = useState("");
  const [personalDrive, setPersonalDrive] = useState<OneDriveResource | null>(null);
  const [sites, setSites] = useState<
    { site_id: string; name: string; url?: string | null }[]
  >([]);
  const [activeSiteDrives, setActiveSiteDrives] = useState<
    { drive_id: string; name: string; kind: "site"; site_id: string }[]
  >([]);
  const [activeSiteId, setActiveSiteId] = useState<string | null>(null);
  const [picked, setPicked] = useState<Map<string, OneDriveResource>>(
    new Map(selected.map((r) => [`${r.kind}-${r.drive_id}`, r])),
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPicked(new Map(selected.map((r) => [`${r.kind}-${r.drive_id}`, r])));
    (async () => {
      const r = await fetch("/api/integrations/onedrive/personal-drive");
      if (r.ok) {
        const json = (await r.json()) as { drive: OneDriveResource | null };
        setPersonalDrive(json.drive);
      }
    })();
  }, [open, selected]);

  useEffect(() => {
    if (!open || tab !== "sites") return;
    const handle = setTimeout(async () => {
      const r = await fetch(
        `/api/integrations/onedrive/sites?q=${encodeURIComponent(siteQuery)}`,
      );
      if (r.ok) setSites((await r.json()).sites || []);
    }, 250);
    return () => clearTimeout(handle);
  }, [open, tab, siteQuery]);

  const togglePersonal = () => {
    if (!personalDrive) return;
    setPicked((prev) => {
      const next = new Map(prev);
      const key = `drive-${personalDrive.drive_id}`;
      if (next.has(key)) next.delete(key);
      else next.set(key, personalDrive);
      return next;
    });
  };

  const loadSiteDrives = async (siteId: string) => {
    setActiveSiteId(siteId);
    const r = await fetch(
      `/api/integrations/onedrive/sites/${encodeURIComponent(siteId)}/drives`,
    );
    if (r.ok) setActiveSiteDrives((await r.json()).drives || []);
  };

  const toggleSiteDrive = (
    d: { drive_id: string; name: string; site_id: string },
  ) => {
    setPicked((prev) => {
      const next = new Map(prev);
      const key = `site-${d.drive_id}`;
      if (next.has(key)) next.delete(key);
      else
        next.set(key, {
          kind: "site",
          drive_id: d.drive_id,
          site_id: d.site_id,
          name: d.name,
        });
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/integrations/onedrive/resources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resources: [...picked.values()] }),
      });
      if (!res.ok) {
        toast.error("Save failed");
        return;
      }
      toast.success("Sources saved");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Pick Microsoft sources</DialogTitle>
          <DialogDescription>
            OneDrive Business gives you the connecting admin's drive.
            SharePoint sites expose their document libraries (one or many per
            site).
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex gap-2 border-b border-border pb-2 text-xs">
            <button
              className={`rounded px-2 py-1 ${tab === "drives" ? "bg-brand-tint font-semibold text-brand" : "text-muted-foreground"}`}
              onClick={() => setTab("drives")}
            >
              OneDrive
            </button>
            <button
              className={`rounded px-2 py-1 ${tab === "sites" ? "bg-brand-tint font-semibold text-brand" : "text-muted-foreground"}`}
              onClick={() => setTab("sites")}
            >
              SharePoint sites
            </button>
          </div>
          {tab === "drives" ? (
            <div className="space-y-2">
              {personalDrive ? (
                <label className="flex cursor-pointer items-center gap-2 rounded-md border border-border p-3 text-xs">
                  <Checkbox
                    checked={picked.has(`drive-${personalDrive.drive_id}`)}
                    onCheckedChange={togglePersonal}
                  />
                  <HardDrive className="h-4 w-4 text-muted-foreground" />
                  <div>
                    <p className="font-medium">{personalDrive.name}</p>
                    <p className="text-[11px] text-muted-foreground">
                      OneDrive Business — admin account
                    </p>
                  </div>
                </label>
              ) : (
                <p className="px-3 py-6 text-xs text-muted-foreground">
                  Loading drive…
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <Input
                placeholder="Filter sites…"
                value={siteQuery}
                onChange={(e) => setSiteQuery(e.target.value)}
              />
              <div className="grid grid-cols-2 gap-2">
                <ul className="max-h-[40vh] divide-y divide-border overflow-auto rounded-xl border border-border text-xs">
                  {sites.map((s) => (
                    <li
                      key={s.site_id}
                      className={`cursor-pointer px-2 py-2 hover:bg-muted ${activeSiteId === s.site_id ? "bg-muted" : ""}`}
                      onClick={() => loadSiteDrives(s.site_id)}
                    >
                      <p className="truncate font-medium">{s.name}</p>
                      <p className="truncate text-[10px] text-muted-foreground">
                        {s.url}
                      </p>
                    </li>
                  ))}
                </ul>
                <ul className="max-h-[40vh] divide-y divide-border overflow-auto rounded-xl border border-border text-xs">
                  {!activeSiteId ? (
                    <li className="px-2 py-3 text-muted-foreground">
                      Select a site to see its libraries
                    </li>
                  ) : activeSiteDrives.length === 0 ? (
                    <li className="px-2 py-3 text-muted-foreground">
                      No libraries visible.
                    </li>
                  ) : (
                    activeSiteDrives.map((d) => (
                      <li
                        key={d.drive_id}
                        className="flex items-center gap-2 px-2 py-2"
                      >
                        <Checkbox
                          checked={picked.has(`site-${d.drive_id}`)}
                          onCheckedChange={() => toggleSiteDrive(d)}
                        />
                        <span className="truncate">{d.name}</span>
                      </li>
                    ))
                  )}
                </ul>
              </div>
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            {picked.size} source{picked.size === 1 ? "" : "s"} selected
          </p>
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Confluence
// ──────────────────────────────────────────────────────────────────────────

interface ConfluenceCloud {
  cloud_id: string;
  name?: string;
  url?: string;
}

interface ConfluenceResource {
  cloud_id: string;
  cloud_name?: string;
  space_id: string;
  space_key?: string;
  space_name?: string;
}

export function ConfluenceCard({
  status,
  onChanged,
}: {
  status?: ConfluenceStatus;
  onChanged: () => void;
}) {
  const [syncing, setSyncing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  if (!status || !status.available) {
    return (
      <Card
        icon={<FileText className="h-4 w-4" />}
        title="Confluence"
        description="Not configured. Ask your operator to set ATLASSIAN_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<FileText className="h-4 w-4" />}
        title="Confluence"
        description="Pull pages and attachments from selected Confluence spaces. Polls every 15 minutes."
      >
        <Button
          size="sm"
          onClick={() => startConnect("/api/integrations/confluence/connect")}
        >
          Connect Confluence
        </Button>
      </Card>
    );
  }

  const clouds =
    ((status.metadata as { clouds?: ConfluenceCloud[] })?.clouds || []) as ConfluenceCloud[];
  const resources = (status.resources || []) as ConfluenceResource[];

  return (
    <Card
      icon={<FileText className="h-4 w-4" />}
      title={`Confluence · ${clouds[0]?.name || "Connected"}`}
      description={
        status.last_synced_at
          ? `Last synced ${new Date(status.last_synced_at).toLocaleString()}`
          : "Not synced yet"
      }
    >
      <div className="space-y-3">
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Connected spaces ({resources.length})
          </p>
          {resources.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No spaces selected. Click{" "}
              <span className="font-medium">Manage spaces</span> to pick.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {resources.map((r) => (
                <span
                  key={`${r.cloud_id}-${r.space_id}`}
                  className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 py-0.5 pl-2 pr-2 text-xs"
                  title={r.space_key}
                >
                  <FileText className="h-3 w-3 text-muted-foreground" />
                  <span className="max-w-[200px] truncate">
                    {r.space_name || r.space_key || r.space_id}
                  </span>
                </span>
              ))}
            </div>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={() => setPickerOpen(true)}>
          <Settings2 className="h-3.5 w-3.5" /> Manage spaces
        </Button>
        <StatusFooter
          status={status}
          syncing={syncing}
          onSync={async () => {
            setSyncing(true);
            try {
              if (await genericSync("/api/integrations/confluence/sync")) onChanged();
            } finally {
              setSyncing(false);
            }
          }}
          onDisconnect={async () => {
            if (
              await genericDisconnect("/api/integrations/confluence", "Confluence")
            )
              onChanged();
          }}
        />
      </div>
      <ConfluencePicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        clouds={clouds}
        selected={resources}
        onSaved={onChanged}
      />
    </Card>
  );
}

function ConfluencePicker({
  open,
  onOpenChange,
  clouds,
  selected,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  clouds: ConfluenceCloud[];
  selected: ConfluenceResource[];
  onSaved: () => void;
}) {
  const [activeCloud, setActiveCloud] = useState<string>(clouds[0]?.cloud_id || "");
  const [spaces, setSpaces] = useState<
    { space_id: string; space_key?: string; name: string }[]
  >([]);
  const [picked, setPicked] = useState<Map<string, ConfluenceResource>>(
    new Map(selected.map((r) => [`${r.cloud_id}:${r.space_id}`, r])),
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPicked(new Map(selected.map((r) => [`${r.cloud_id}:${r.space_id}`, r])));
    if (!activeCloud && clouds[0]) setActiveCloud(clouds[0].cloud_id);
  }, [open, selected, clouds, activeCloud]);

  useEffect(() => {
    if (!open || !activeCloud) return;
    (async () => {
      const r = await fetch(
        `/api/integrations/confluence/spaces?cloud_id=${encodeURIComponent(activeCloud)}`,
      );
      if (r.ok) setSpaces((await r.json()).spaces || []);
    })();
  }, [open, activeCloud]);

  const toggle = (s: { space_id: string; space_key?: string; name: string }) => {
    setPicked((prev) => {
      const next = new Map(prev);
      const key = `${activeCloud}:${s.space_id}`;
      if (next.has(key)) next.delete(key);
      else {
        const cloud = clouds.find((c) => c.cloud_id === activeCloud);
        next.set(key, {
          cloud_id: activeCloud,
          cloud_name: cloud?.name,
          space_id: s.space_id,
          space_key: s.space_key,
          space_name: s.name,
        });
      }
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/integrations/confluence/resources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resources: [...picked.values()] }),
      });
      if (!res.ok) {
        toast.error("Save failed");
        return;
      }
      toast.success("Spaces saved");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Pick Confluence spaces</DialogTitle>
          <DialogDescription>
            Pages and PDF/DOCX attachments under selected spaces will sync
            every 15 minutes.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {clouds.length > 1 ? (
            <div className="flex flex-wrap gap-1">
              {clouds.map((c) => (
                <button
                  key={c.cloud_id}
                  onClick={() => setActiveCloud(c.cloud_id)}
                  className={`rounded px-2 py-1 text-xs ${activeCloud === c.cloud_id ? "bg-brand-tint font-semibold text-brand" : "text-muted-foreground"}`}
                >
                  {c.name || c.cloud_id}
                </button>
              ))}
            </div>
          ) : null}
          <ul className="max-h-[55vh] divide-y divide-border overflow-auto rounded-xl border border-border">
            {spaces.length === 0 ? (
              <li className="px-3 py-6 text-xs text-muted-foreground">
                No spaces visible — the integration may not have access yet.
              </li>
            ) : (
              spaces.map((s) => {
                const key = `${activeCloud}:${s.space_id}`;
                const checked = picked.has(key);
                return (
                  <li
                    key={s.space_id}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs hover:bg-muted/50"
                    onClick={() => toggle(s)}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => toggle(s)}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{s.name}</p>
                      <p className="text-[10px] text-muted-foreground">
                        {s.space_key}
                      </p>
                    </div>
                  </li>
                );
              })
            )}
          </ul>
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// GitHub
// ──────────────────────────────────────────────────────────────────────────

interface GitHubResource {
  repo_id: number;
  full_name: string;
  default_branch?: string;
  include_markdown?: boolean;
  include_issues?: boolean;
  include_discussions?: boolean;
}

export function GitHubCard({
  status,
  onChanged,
}: {
  status?: GitHubStatus;
  onChanged: () => void;
}) {
  const [syncing, setSyncing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  if (!status || !status.available) {
    return (
      <Card
        icon={<Github className="h-4 w-4" />}
        title="GitHub"
        description="Not configured. Ask your operator to set GITHUB_APP_ID + GITHUB_APP_SLUG."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<Github className="h-4 w-4" />}
        title="GitHub"
        description="Ingest README files, /docs trees, and Issues+Discussions from selected repos. Polls every 15 minutes."
      >
        <Button
          size="sm"
          onClick={() => startConnect("/api/integrations/github/connect")}
        >
          Install GitHub App
        </Button>
      </Card>
    );
  }

  const account = status.metadata as {
    account_login?: string;
    account_type?: string;
  };
  const resources = (status.resources || []) as GitHubResource[];

  return (
    <Card
      icon={<Github className="h-4 w-4" />}
      title={`GitHub · ${account?.account_login || "Connected"}`}
      description={
        status.last_synced_at
          ? `Last synced ${new Date(status.last_synced_at).toLocaleString()}`
          : "Not synced yet"
      }
    >
      <div className="space-y-3">
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Connected repositories ({resources.length})
          </p>
          {resources.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No repositories selected. Click{" "}
              <span className="font-medium">Manage repos</span> to pick.
            </p>
          ) : (
            <ul className="space-y-1 text-xs">
              {resources.map((r) => (
                <li
                  key={r.repo_id}
                  className="flex items-center justify-between rounded border border-border px-2 py-1"
                >
                  <span className="truncate font-medium">{r.full_name}</span>
                  <span className="text-[10px] text-muted-foreground">
                    {[
                      r.include_markdown ? "docs" : null,
                      r.include_issues ? "issues" : null,
                      r.include_discussions ? "discussions" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={() => setPickerOpen(true)}>
          <Settings2 className="h-3.5 w-3.5" /> Manage repos
        </Button>
        <StatusFooter
          status={status}
          syncing={syncing}
          onSync={async () => {
            setSyncing(true);
            try {
              if (await genericSync("/api/integrations/github/sync")) onChanged();
            } finally {
              setSyncing(false);
            }
          }}
          onDisconnect={async () => {
            if (await genericDisconnect("/api/integrations/github", "GitHub"))
              onChanged();
          }}
        />
        <p className="text-[11px] text-muted-foreground">
          Disconnecting removes your selection here, but does NOT uninstall
          the GitHub App. To fully revoke, visit{" "}
          <code className="rounded bg-muted px-1">
            github.com/settings/installations
          </code>
          .
        </p>
      </div>
      <GitHubPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        selected={resources}
        onSaved={onChanged}
      />
    </Card>
  );
}

function GitHubPicker({
  open,
  onOpenChange,
  selected,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  selected: GitHubResource[];
  onSaved: () => void;
}) {
  const [repos, setRepos] = useState<
    { repo_id: number; full_name: string; default_branch: string }[]
  >([]);
  const [picked, setPicked] = useState<Map<number, GitHubResource>>(
    new Map(selected.map((r) => [r.repo_id, r])),
  );
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) return;
    setPicked(new Map(selected.map((r) => [r.repo_id, r])));
    (async () => {
      const r = await fetch("/api/integrations/github/repos");
      if (r.ok) setRepos((await r.json()).repos || []);
    })();
  }, [open, selected]);

  const toggle = (repo: {
    repo_id: number;
    full_name: string;
    default_branch: string;
  }) => {
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(repo.repo_id)) next.delete(repo.repo_id);
      else
        next.set(repo.repo_id, {
          ...repo,
          include_markdown: true,
          include_issues: true,
          include_discussions: true,
        });
      return next;
    });
  };

  const setFlag = (repo_id: number, key: keyof GitHubResource, value: boolean) => {
    setPicked((prev) => {
      const next = new Map(prev);
      const existing = next.get(repo_id);
      if (!existing) return prev;
      next.set(repo_id, { ...existing, [key]: value });
      return next;
    });
  };

  const filtered = repos.filter((r) =>
    !query.trim() ? true : r.full_name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/integrations/github/resources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resources: [...picked.values()] }),
      });
      if (!res.ok) {
        toast.error("Save failed");
        return;
      }
      toast.success("Repositories saved");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Pick GitHub repositories</DialogTitle>
          <DialogDescription>
            Only repos the GitHub App is installed on appear here. Add more
            via your GitHub App settings, then refresh this dialog.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Input
            placeholder="Filter repos…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <ul className="max-h-[55vh] divide-y divide-border overflow-auto rounded-xl border border-border">
            {filtered.length === 0 ? (
              <li className="px-3 py-6 text-xs text-muted-foreground">
                No repositories visible.
              </li>
            ) : (
              filtered.map((r) => {
                const sel = picked.get(r.repo_id);
                return (
                  <li key={r.repo_id} className="px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <Checkbox
                        checked={!!sel}
                        onCheckedChange={() => toggle(r)}
                      />
                      <span className="flex-1 truncate font-medium">
                        {r.full_name}
                      </span>
                    </div>
                    {sel ? (
                      <div className="ml-6 mt-1 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                        <label className="flex items-center gap-1">
                          <Checkbox
                            checked={!!sel.include_markdown}
                            onCheckedChange={(v) =>
                              setFlag(r.repo_id, "include_markdown", v === true)
                            }
                          />{" "}
                          Docs (.md / /docs)
                        </label>
                        <label className="flex items-center gap-1">
                          <Checkbox
                            checked={!!sel.include_issues}
                            onCheckedChange={(v) =>
                              setFlag(r.repo_id, "include_issues", v === true)
                            }
                          />{" "}
                          Issues
                        </label>
                        <label className="flex items-center gap-1">
                          <Checkbox
                            checked={!!sel.include_discussions}
                            onCheckedChange={(v) =>
                              setFlag(r.repo_id, "include_discussions", v === true)
                            }
                          />{" "}
                          Discussions
                        </label>
                      </div>
                    ) : null}
                  </li>
                );
              })
            )}
          </ul>
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Dropbox
// ──────────────────────────────────────────────────────────────────────────

interface DropboxResource {
  path: string;
  name: string;
}

export function DropboxCard({
  status,
  onChanged,
}: {
  status?: DropboxStatus;
  onChanged: () => void;
}) {
  const [syncing, setSyncing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  if (!status || !status.available) {
    return (
      <Card
        icon={<HardDrive className="h-4 w-4" />}
        title="Dropbox"
        description="Not configured. Ask your operator to set DROPBOX_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<HardDrive className="h-4 w-4" />}
        title="Dropbox"
        description="Pull files from selected Dropbox folders. Polls every 10 minutes."
      >
        <Button
          size="sm"
          onClick={() => startConnect("/api/integrations/dropbox/connect")}
        >
          Connect Dropbox
        </Button>
      </Card>
    );
  }

  const account = (status.metadata as { account?: { email?: string; name?: string; team_name?: string } })
    ?.account;
  const resources = (status.resources || []) as DropboxResource[];

  return (
    <Card
      icon={<HardDrive className="h-4 w-4" />}
      title={`Dropbox · ${account?.team_name || account?.name || account?.email || "Connected"}`}
      description={
        status.last_synced_at
          ? `Last synced ${new Date(status.last_synced_at).toLocaleString()}`
          : "Not synced yet"
      }
    >
      <div className="space-y-3">
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Connected folders ({resources.length})
          </p>
          {resources.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No folders selected. Click{" "}
              <span className="font-medium">Manage folders</span> to pick.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {resources.map((r) => (
                <span
                  key={r.path}
                  className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 py-0.5 pl-2 pr-2 text-xs"
                  title={r.path}
                >
                  <HardDrive className="h-3 w-3 text-muted-foreground" />
                  <span className="max-w-[200px] truncate">{r.name}</span>
                </span>
              ))}
            </div>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={() => setPickerOpen(true)}>
          <Settings2 className="h-3.5 w-3.5" /> Manage folders
        </Button>
        <StatusFooter
          status={status}
          syncing={syncing}
          onSync={async () => {
            setSyncing(true);
            try {
              if (await genericSync("/api/integrations/dropbox/sync")) onChanged();
            } finally {
              setSyncing(false);
            }
          }}
          onDisconnect={async () => {
            if (await genericDisconnect("/api/integrations/dropbox", "Dropbox"))
              onChanged();
          }}
        />
      </div>
      <DropboxPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        selected={resources}
        onSaved={onChanged}
      />
    </Card>
  );
}

function DropboxPicker({
  open,
  onOpenChange,
  selected,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  selected: DropboxResource[];
  onSaved: () => void;
}) {
  // Dropbox uses "" for the root path, and "/Marketing/Q4" for nested. The
  // picker presents one level at a time and lets the user descend by clicking
  // a folder name — same UX as the OS file picker.
  const [path, setPath] = useState("");
  const [folders, setFolders] = useState<
    { path: string; name: string; id?: string }[]
  >([]);
  const [picked, setPicked] = useState<Map<string, DropboxResource>>(
    new Map(selected.map((r) => [r.path, r])),
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPicked(new Map(selected.map((r) => [r.path, r])));
  }, [open, selected]);

  useEffect(() => {
    if (!open) return;
    (async () => {
      const r = await fetch(
        `/api/integrations/dropbox/folders?path=${encodeURIComponent(path)}`,
      );
      if (r.ok) setFolders((await r.json()).folders || []);
    })();
  }, [open, path]);

  const toggle = (f: { path: string; name: string }) => {
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(f.path)) next.delete(f.path);
      else next.set(f.path, { path: f.path, name: f.name });
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/integrations/dropbox/resources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resources: [...picked.values()] }),
      });
      if (!res.ok) {
        toast.error("Save failed");
        return;
      }
      toast.success("Folders saved");
      onSaved();
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  // Break the current path into breadcrumbs.
  const parts = path.split("/").filter(Boolean);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Pick Dropbox folders</DialogTitle>
          <DialogDescription>
            Files in selected folders (recursive) are ingested. Click a folder
            name to descend; tick the checkbox to add it to the sync set.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1 text-xs">
            <button
              className="rounded px-1 hover:bg-muted"
              onClick={() => setPath("")}
            >
              /
            </button>
            {parts.map((p, i) => (
              <span key={i} className="flex items-center gap-1">
                <span className="text-muted-foreground">/</span>
                <button
                  className="rounded px-1 hover:bg-muted"
                  onClick={() => setPath("/" + parts.slice(0, i + 1).join("/"))}
                >
                  {p}
                </button>
              </span>
            ))}
          </div>
          <ul className="max-h-[55vh] divide-y divide-border overflow-auto rounded-xl border border-border">
            {folders.length === 0 ? (
              <li className="px-3 py-6 text-xs text-muted-foreground">
                No subfolders here.
              </li>
            ) : (
              folders.map((f) => (
                <li
                  key={f.path}
                  className="flex items-center gap-2 px-3 py-2 text-xs"
                >
                  <Checkbox
                    checked={picked.has(f.path)}
                    onCheckedChange={() => toggle(f)}
                  />
                  <button
                    className="min-w-0 flex-1 truncate text-left hover:underline"
                    onClick={() => setPath(f.path)}
                  >
                    📁 {f.name}
                  </button>
                </li>
              ))
            )}
          </ul>
          <p className="text-xs text-muted-foreground">
            {picked.size} folder{picked.size === 1 ? "" : "s"} selected
          </p>
        </div>
        <DialogFooter>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Zoom
//
// No picker, no manual sync — a webhook (recording.transcript_completed)
// covers every cloud recording account-wide the moment its transcript is
// ready. Ingestion is gated per host: only meetings whose host opted in
// ("Sync my Zoom meeting transcripts" below) are ingested, owned by that
// host, and private to them until they publish. The connecting admin is
// auto-opted-in at connect time.
// ──────────────────────────────────────────────────────────────────────────

function ZoomOptinToggle() {
  const [optedIn, setOptedIn] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/integrations/zoom/transcript-optin")
      .then((res) => (res.ok ? res.json() : { opted_in: false }))
      .then((data: { opted_in?: boolean }) => {
        if (!cancelled) setOptedIn(Boolean(data.opted_in));
      })
      .catch(() => {
        if (!cancelled) setOptedIn(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function toggle(next: boolean) {
    setSaving(true);
    const res = await fetch("/api/integrations/zoom/transcript-optin", {
      method: next ? "PUT" : "DELETE",
    });
    setSaving(false);
    if (!res.ok) {
      toast.error("Could not update your transcript sync preference");
      return;
    }
    setOptedIn(next);
    toast.success(
      next
        ? "Your Zoom meeting transcripts will now sync (visible only to you)."
        : "Your Zoom meeting transcripts will no longer sync.",
    );
  }

  return (
    <div className="flex items-start gap-2">
      <Checkbox
        id="zoom-transcript-optin"
        checked={optedIn === true}
        disabled={optedIn === null || saving}
        onCheckedChange={(v) => toggle(v === true)}
      />
      <label htmlFor="zoom-transcript-optin" className="cursor-pointer">
        <span className="block text-xs font-medium">
          Sync my Zoom meeting transcripts
        </span>
        <span className="block text-[11px] text-muted-foreground">
          Only meetings you host are ingested, and they stay private to you
          until you share them with the workspace.
        </span>
      </label>
    </div>
  );
}

function ZoomAttendeeOptinToggle() {
  const [optedIn, setOptedIn] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/integrations/zoom/attendee-optin")
      .then((res) => (res.ok ? res.json() : { opted_in: false }))
      .then((data: { opted_in?: boolean }) => {
        if (!cancelled) setOptedIn(Boolean(data.opted_in));
      })
      .catch(() => {
        if (!cancelled) setOptedIn(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function toggle(next: boolean) {
    setSaving(true);
    const res = await fetch("/api/integrations/zoom/attendee-optin", {
      method: next ? "PUT" : "DELETE",
    });
    setSaving(false);
    if (!res.ok) {
      toast.error("Could not update your attendee sharing preference");
      return;
    }
    setOptedIn(next);
    toast.success(
      next
        ? "You'll now automatically get transcripts of meetings you attend."
        : "You'll no longer automatically receive transcripts as an attendee.",
    );
  }

  return (
    <div className="flex items-start gap-2">
      <Checkbox
        id="zoom-attendee-optin"
        checked={optedIn === true}
        disabled={optedIn === null || saving}
        onCheckedChange={(v) => toggle(v === true)}
      />
      <label htmlFor="zoom-attendee-optin" className="cursor-pointer">
        <span className="block text-xs font-medium">
          Auto-share transcripts of meetings I attend
        </span>
        <span className="block text-[11px] text-muted-foreground">
          When a meeting's host has transcript sync on, you'll get read
          access to it automatically if you were on the call and opted in
          here — no separate publish step needed.
        </span>
      </label>
    </div>
  );
}

export function ZoomCard({
  status,
  onChanged,
}: {
  status?: ZoomStatus;
  onChanged: () => void;
}) {
  if (!status || !status.available) {
    return (
      <Card
        icon={<Video className="h-4 w-4" />}
        title="Zoom"
        description="Not configured. Ask your operator to set ZOOM_CLIENT_ID."
      >
        <p className="text-xs text-muted-foreground">Unavailable on this deploy.</p>
      </Card>
    );
  }

  if (!status.connected) {
    return (
      <Card
        icon={<Video className="h-4 w-4" />}
        title="Zoom"
        description="Auto-ingest cloud-recording transcripts the moment Zoom finishes processing them — routed through decision/action-item extraction, not just raw text. Each teammate opts in individually; transcripts stay private to their host."
      >
        <Button size="sm" onClick={() => startConnect("/api/integrations/zoom/connect")}>
          Connect Zoom
        </Button>
        <p className="mt-2 text-xs text-muted-foreground">
          Admin consent required. Requires cloud recording + audio transcript
          enabled on the Zoom account.
        </p>
      </Card>
    );
  }

  const account = status.metadata as { email?: string } | undefined;

  return (
    <Card
      icon={<Video className="h-4 w-4" />}
      title={`Zoom · ${account?.email || "Connected"}`}
      description="Listening for new transcripts — no manual sync needed."
    >
      <div className="space-y-3">
        <ZoomOptinToggle />
        <ZoomAttendeeOptinToggle />
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            if (await genericDisconnect("/api/integrations/zoom", "Zoom")) onChanged();
          }}
        >
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
        {status.last_error ? (
          <p
            className="truncate text-[11px] text-amber-ink"
            title={status.last_error}
          >
            ⚠ {status.last_error}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
