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
  Loader2,
  Mail,
  MessageSquare,
  Plus,
  RefreshCw,
  Trash2,
  Unplug,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

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
interface SlackStatus {
  available: boolean;
  connected: boolean;
  workspace_name: string | null;
  installed_at: string | null;
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
  const [adding, setAdding] = useState("");
  const [syncing, setSyncing] = useState(false);

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
      <div className="space-y-2">
        {status.folder_ids.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No folders configured yet. Add a Drive folder ID to start syncing.
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {status.folder_ids.map((id) => (
              <li
                key={id}
                className="flex items-center gap-2 px-3 py-2 text-xs"
              >
                <Folder className="h-3.5 w-3.5 text-muted-foreground" />
                <code className="flex-1 truncate">{id}</code>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    const res = await fetch(
                      `/api/integrations/drive/folders/${encodeURIComponent(id)}`,
                      { method: "DELETE" },
                    );
                    if (!res.ok) return toast.error("Remove failed");
                    onChanged();
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-center gap-2">
          <Input
            placeholder="Drive folder ID (e.g. 1abc…XYZ)"
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
          />
          <Button
            size="sm"
            onClick={async () => {
              const id = adding.trim();
              if (!id) return;
              const res = await fetch("/api/integrations/drive/folders", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ folder_id: id }),
              });
              if (!res.ok) return toast.error("Add failed");
              setAdding("");
              onChanged();
            }}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex items-center gap-2 pt-1">
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
    </Card>
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
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<
    { id: string; title: string; url?: string }[]
  >([]);
  const [chosen, setChosen] = useState<{ id: string; title: string }[]>(selected);

  const runSearch = async () => {
    setSearching(true);
    try {
      const res = await fetch(
        `/api/integrations/notion/pages?q=${encodeURIComponent(query)}`,
      );
      if (!res.ok) return toast.error("Search failed");
      const json = await res.json();
      setResults(json.pages || []);
    } finally {
      setSearching(false);
    }
  };

  const toggle = (p: { id: string; title: string }) =>
    setChosen((prev) =>
      prev.some((x) => x.id === p.id)
        ? prev.filter((x) => x.id !== p.id)
        : [...prev, { id: p.id, title: p.title }],
    );

  const save = async () => {
    const res = await fetch("/api/integrations/notion/pages", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pages: chosen }),
    });
    if (!res.ok) return toast.error("Save failed");
    toast.success("Pages saved");
    onChanged();
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Input
          placeholder="Search your Notion workspace…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button size="sm" onClick={runSearch} disabled={searching}>
          {searching ? "…" : "Search"}
        </Button>
      </div>
      {results.length > 0 ? (
        <ul className="max-h-48 divide-y divide-border overflow-auto rounded-md border border-border">
          {results.map((p) => {
            const isOn = chosen.some((x) => x.id === p.id);
            return (
              <li
                key={p.id}
                className="flex items-center gap-2 px-3 py-2 text-xs"
              >
                <button
                  type="button"
                  onClick={() => toggle({ id: p.id, title: p.title })}
                  className={
                    "rounded-sm border px-1.5 py-0.5 text-[10px] " +
                    (isOn
                      ? "border-emerald-600 bg-emerald-50 text-emerald-700"
                      : "border-border")
                  }
                >
                  {isOn ? "✓ Selected" : "Add"}
                </button>
                <span className="truncate">{p.title}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
      <p className="text-xs text-muted-foreground">
        {chosen.length} page{chosen.length === 1 ? "" : "s"} selected
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save}>Save selection</Button>
        <Button
          variant="outline"
          size="sm"
          onClick={async () => {
            const res = await fetch("/api/integrations/notion/sync", {
              method: "POST",
            });
            if (!res.ok) return toast.error("Sync failed");
            toast.success("Sync triggered");
            onChanged();
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
    </div>
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
      <Button
        size="sm"
        variant="ghost"
        onClick={async () => {
          if (!confirm("Disconnect Slack?")) return;
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
      <p className="mt-2 text-xs text-muted-foreground">
        Try{" "}
        <code className="rounded bg-muted px-1">/brain what is our refund policy?</code>{" "}
        in any channel.
      </p>
    </Card>
  );
}
