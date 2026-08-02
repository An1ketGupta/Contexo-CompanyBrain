"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { ArrowLeft, Check, Copy, Loader2, Plus, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface Webhook {
  id: string;
  name: string;
  url: string;
  events: string[];
  is_active: boolean;
  secret: string | null;
  last_status: number | null;
  last_triggered_at: string | null;
  created_at: string;
}

interface Delivery {
  id: string;
  event: string;
  status_code: number | null;
  error: string | null;
  attempt: number;
  delivered_at: string;
  response_body: string | null;
}

const ALL_EVENTS = [
  { value: "document.uploaded", label: "Document uploaded" },
  { value: "document.processed", label: "Document processed" },
  { value: "document.failed", label: "Document failed" },
  { value: "query.completed", label: "Query completed" },
  { value: "message.feedback.negative", label: "Negative feedback on a message" },
  { value: "approval.requested", label: "Approval requested" },
  { value: "approval.decided", label: "Approval decided" },
  { value: "compliance.acknowledged", label: "Compliance acknowledged" },
  { value: "agent.completed", label: "Agent run completed" },
  { value: "agent.failed", label: "Agent run failed" },
  { value: "employee.joined", label: "Employee joined" },
  { value: "output.delivered", label: "Output delivered" },
  { value: "output.failed", label: "Output failed" },
];

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function WebhooksPage() {
  const { data, error, isLoading, mutate } = useSWR<{ webhooks: Webhook[] }>(
    "/api/webhooks",
    fetcher,
  );

  const [createOpen, setCreateOpen] = useState(false);
  const [revealedSecret, setRevealedSecret] = useState<{
    id: string;
    secret: string;
  } | null>(null);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to settings
      </Link>

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Webhooks</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Get notified when documents finish processing or chat turns complete.
            Useful for Zapier flows and custom integrations.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)} size="sm">
          <Plus className="h-4 w-4" /> New webhook
        </Button>
      </header>

      {isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : error ? (
        <p className="text-sm text-destructive">{(error as Error).message}</p>
      ) : !data || data.webhooks.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-background p-6 text-center text-sm text-muted-foreground">
          No webhooks yet. Add one to start receiving events.
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border bg-background">
          {data.webhooks.map((wh) => (
            <WebhookRow
              key={wh.id}
              webhook={wh}
              onChanged={() => mutate()}
            />
          ))}
        </ul>
      )}

      <CreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(secret, id) => {
          setRevealedSecret(secret ? { id, secret } : null);
          mutate();
        }}
      />

      <SecretRevealDialog
        open={revealedSecret !== null}
        secret={revealedSecret?.secret ?? null}
        onClose={() => setRevealedSecret(null)}
      />
    </div>
  );
}

function WebhookRow({
  webhook,
  onChanged,
}: {
  webhook: Webhook;
  onChanged: () => void;
}) {
  const [showLog, setShowLog] = useState(false);
  const { data: log, mutate: refreshLog } = useSWR<{ deliveries: Delivery[] }>(
    showLog ? `/api/webhooks/${webhook.id}/deliveries` : null,
    fetcher,
  );
  const [testing, setTesting] = useState(false);
  const [deleting, setDeleting] = useState(false);

  return (
    <li className="px-4 py-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{webhook.name}</p>
          <p className="truncate text-xs text-muted-foreground">{webhook.url}</p>
        </div>
        <StatusPill status={webhook.last_status} active={webhook.is_active} />
        <Button
          variant="ghost"
          size="sm"
          disabled={testing}
          onClick={async () => {
            setTesting(true);
            try {
              const res = await fetch(`/api/webhooks/${webhook.id}/test`, {
                method: "POST",
              });
              if (!res.ok) throw new Error(`Failed (${res.status})`);
              toast.success("Test event queued — check the delivery log.");
              setShowLog(true);
              setTimeout(() => refreshLog(), 2_000);
            } catch (e) {
              toast.error((e as Error).message);
            } finally {
              setTesting(false);
            }
          }}
        >
          <Send className="h-3.5 w-3.5" /> Test
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={deleting}
          onClick={async () => {
            if (!confirm(`Delete webhook "${webhook.name}"?`)) return;
            setDeleting(true);
            try {
              const res = await fetch(`/api/webhooks/${webhook.id}`, {
                method: "DELETE",
              });
              if (!res.ok) throw new Error(`Failed (${res.status})`);
              toast.success("Webhook deleted");
              onChanged();
            } catch (e) {
              toast.error((e as Error).message);
            } finally {
              setDeleting(false);
            }
          }}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {webhook.events.map((e) => (
          <span
            key={e}
            className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
          >
            {e}
          </span>
        ))}
      </div>

      <button
        type="button"
        className="mt-2 text-xs text-muted-foreground hover:text-foreground"
        onClick={() => setShowLog((v) => !v)}
      >
        {showLog ? "Hide" : "Show"} delivery log
      </button>

      {showLog && log ? (
        <div className="mt-2 rounded-md border border-border bg-muted/30 p-2">
          {log.deliveries.length === 0 ? (
            <p className="text-xs text-muted-foreground">No deliveries yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {log.deliveries.map((d) => (
                <li key={d.id} className="flex items-center gap-2 py-1.5 text-xs">
                  <span
                    className={
                      d.status_code && d.status_code >= 200 && d.status_code < 300
                        ? "rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700"
                        : "rounded bg-red-100 px-1.5 py-0.5 text-red-700"
                    }
                  >
                    {d.status_code ?? "ERR"}
                  </span>
                  <span className="font-mono text-muted-foreground">{d.event}</span>
                  <span className="ml-auto text-muted-foreground">
                    attempt {d.attempt} · {new Date(d.delivered_at).toLocaleTimeString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </li>
  );
}

function StatusPill({
  status,
  active,
}: {
  status: number | null;
  active: boolean;
}) {
  if (!active)
    return (
      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
        Disabled
      </span>
    );
  if (status === null)
    return (
      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
        Untried
      </span>
    );
  if (status >= 200 && status < 300)
    return (
      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
        {status} OK
      </span>
    );
  return (
    <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700">
      {status}
    </span>
  );
}

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (secret: string | null, id: string) => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>([
    "document.processed",
    "document.failed",
  ]);
  const [generateSecret, setGenerateSecret] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const toggle = (e: string) =>
    setEvents((prev) =>
      prev.includes(e) ? prev.filter((x) => x !== e) : [...prev, e],
    );

  const submit = async () => {
    if (!name.trim() || !url.trim() || events.length === 0) return;
    setSubmitting(true);
    try {
      const res = await fetch("/api/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          url,
          events,
          generate_secret: generateSecret,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      const json = await res.json();
      toast.success("Webhook created");
      onOpenChange(false);
      setName("");
      setUrl("");
      onCreated(json.webhook?.secret ?? null, json.webhook?.id ?? "");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New webhook</DialogTitle>
          <DialogDescription>
            POSTs a JSON payload to your URL when matching events fire.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="wh-name">Name</Label>
            <Input
              id="wh-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Zapier sync"
            />
          </div>
          <div>
            <Label htmlFor="wh-url">URL</Label>
            <Input
              id="wh-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://hooks.zapier.com/…"
            />
          </div>
          <div>
            <Label className="mb-2 block">Events</Label>
            <div className="space-y-2">
              {ALL_EVENTS.map((e) => (
                <label
                  key={e.value}
                  className="flex items-center gap-2 text-sm"
                >
                  <Checkbox
                    checked={events.includes(e.value)}
                    onCheckedChange={() => toggle(e.value)}
                  />
                  <code className="text-xs">{e.value}</code>
                  <span className="text-xs text-muted-foreground">
                    {e.label}
                  </span>
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={generateSecret}
              onCheckedChange={(v) => setGenerateSecret(Boolean(v))}
            />
            Generate a signing secret (HMAC-SHA256)
          </label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? "Creating…" : "Create webhook"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SecretRevealDialog({
  open,
  secret,
  onClose,
}: {
  open: boolean;
  secret: string | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save your signing secret</DialogTitle>
          <DialogDescription>
            We won&apos;t show this again. Store it in your receiver to verify
            the <code className="text-xs">X-NirnayaIQ-Signature</code> header.
          </DialogDescription>
        </DialogHeader>
        {secret ? (
          <div className="flex items-center gap-2 rounded-md bg-muted p-3 font-mono text-xs">
            <span className="flex-1 break-all">{secret}</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                await navigator.clipboard.writeText(secret);
                setCopied(true);
                setTimeout(() => setCopied(false), 1_500);
              }}
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </Button>
          </div>
        ) : null}
        <DialogFooter>
          <Button onClick={onClose}>I&apos;ve saved it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
