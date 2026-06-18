"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, Check, Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
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

interface ApiKey {
  id: string;
  name: string;
  scope: string;
  key_prefix: string;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
}

const fetcher = async (url: string) => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function ApiKeysPage() {
  const { data, mutate, isLoading } = useSWR<{ keys: ApiKey[] }>(
    "/api/settings/api-keys",
    fetcher,
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null);

  const visible = (data?.keys ?? []).filter((k) => !k.revoked_at);

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
          <h1 className="text-xl font-semibold tracking-tight">API keys</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Build on top of your knowledge base from your own scripts and
            integrations. Each key is shown once — store it somewhere safe.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" /> New key
        </Button>
      </header>

      {isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : visible.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-background p-6 text-center text-sm text-muted-foreground">
          No keys yet. Create one to start using the Developer API.
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border bg-background">
          {visible.map((k) => (
            <li key={k.id} className="flex items-center gap-3 px-4 py-3">
              <KeyRound className="h-4 w-4 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{k.name}</p>
                <p className="text-xs text-muted-foreground">
                  <code>{k.key_prefix}…</code> · scope {k.scope} ·{" "}
                  {k.last_used_at
                    ? `last used ${new Date(k.last_used_at).toLocaleDateString()}`
                    : "never used"}
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  if (!confirm(`Revoke "${k.name}"? This breaks any script using it.`))
                    return;
                  const res = await fetch(`/api/settings/api-keys/${k.id}`, {
                    method: "DELETE",
                  });
                  if (!res.ok) return toast.error("Revoke failed");
                  toast.success("Key revoked");
                  mutate();
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <CurlExample />

      <AgentTriggerDocs />

      <CreateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(fullKey) => {
          setRevealed(fullKey);
          mutate();
        }}
      />
      <RevealDialog
        open={revealed !== null}
        keyValue={revealed}
        onClose={() => setRevealed(null)}
      />
    </div>
  );
}

function useOrigin() {
  const [origin, setOrigin] = useState("https://your-app.app");
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);
  return origin;
}

function CurlExample() {
  const origin = useOrigin();
  return (
    <section className="space-y-2 rounded-lg border border-border bg-background p-4">
      <p className="text-sm font-medium">Quick start</p>
      <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
{`curl -X POST ${origin}/v1/query \\
  -H "Authorization: Bearer cb_live_…" \\
  -H "Content-Type: application/json" \\
  -d '{"message": "What is our refund policy?"}'`}
      </pre>
      <p className="text-xs text-muted-foreground">
        Returns <code>{`{ output, sources, tool_calls }`}</code>. Rate-limited per
        key per minute, and against your plan&apos;s monthly quota.
      </p>
    </section>
  );
}

function AgentTriggerDocs() {
  const origin = useOrigin();
  return (
    <section className="space-y-3 rounded-lg border border-border bg-background p-4">
      <header>
        <p className="text-sm font-medium">Agent triggers</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Kick off an autonomous agent run from your own scripts, webhooks, or
          HR systems. Each run lands in <code>/admin/agent-runs</code> with a
          full audit trail.
        </p>
      </header>
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          Discover available agents
        </p>
        <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
{`curl ${origin}/v1/agents \\
  -H "Authorization: Bearer cb_live_…"`}
        </pre>
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          Trigger an onboarding agent (e.g. from a BambooHR webhook)
        </p>
        <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
{`curl -X POST ${origin}/v1/agents/onboarding/run \\
  -H "Authorization: Bearer cb_live_…" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: bamboo-evt-12345" \\
  -d '{
    "input": {
      "name": "Sarah Chen",
      "email": "sarah@acme.com",
      "role": "Senior Product Designer",
      "start_date": "2026-07-01",
      "manager_email": "john@acme.com"
    },
    "output_channels": ["email", "slack", "notion"],
    "webhook_url": "https://your-app.com/hooks/cb-agent"
  }'`}
        </pre>
        <p className="text-xs text-muted-foreground">
          Returns <code>{`{ agent_run_id, status, poll_url }`}</code>.{" "}
          <code>Idempotency-Key</code> makes retries safe — the same key
          produces the same <code>agent_run_id</code>.
        </p>
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          Require approval before running
        </p>
        <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
{`# Add approver_email to gate the run behind a human approval.
# The approver must be a workspace member; they get an email + Slack DM
# with magic-link approve/reject buttons. The agent fires on approve.

# ... same request as above, plus:
# "approver_email": "ops-lead@acme.com"`}
        </pre>
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          Poll for status
        </p>
        <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
{`curl ${origin}/v1/agent-runs/<agent_run_id> \\
  -H "Authorization: Bearer cb_live_…"`}
        </pre>
        <p className="text-xs text-muted-foreground">
          Status values: <code>running</code>, <code>pending_approval</code>,{" "}
          <code>completed</code>, <code>failed</code>.
        </p>
      </div>
      <div className="space-y-2">
        <p className="text-xs font-medium text-muted-foreground">
          Webhook signature (per-request callback)
        </p>
        <p className="text-xs text-muted-foreground">
          When <code>webhook_url</code> is set, we POST{" "}
          <code>{`{ event, data, delivered_at, attempt }`}</code> on
          completion. The body is signed in the{" "}
          <code>X-NirnayaIQ-Signature</code> header as{" "}
          <code>sha256=&lt;hex&gt;</code>. The secret is{" "}
          <code>HMAC(INTERNAL_EMAIL_SECRET, api_key_id)</code> — the{" "}
          <code>X-NirnayaIQ-Api-Key-Id</code> header echoes the key id so your
          receiver can derive the same value server-side.
        </p>
      </div>
    </section>
  );
}

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: (fullKey: string) => void;
}) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"org" | "user">("org");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    setSubmitting(true);
    try {
      const res = await fetch("/api/settings/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, scope }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      const json = await res.json();
      onOpenChange(false);
      setName("");
      onCreated(json.key);
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
          <DialogTitle>Create API key</DialogTitle>
          <DialogDescription>
            Pick a name you&apos;ll recognise. Scope controls whether the key
            acts as the org or as a specific user.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label htmlFor="ak-name">Name</Label>
            <Input
              id="ak-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Production server"
            />
          </div>
          <div>
            <Label htmlFor="ak-scope">Scope</Label>
            <select
              id="ak-scope"
              className="block w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              value={scope}
              onChange={(e) => setScope(e.target.value as "org" | "user")}
            >
              <option value="org">Organization</option>
              <option value="user">Just me</option>
            </select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting || !name.trim()}>
            {submitting ? "Creating…" : "Create key"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RevealDialog({
  open,
  keyValue,
  onClose,
}: {
  open: boolean;
  keyValue: string | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Copy your new API key</DialogTitle>
          <DialogDescription className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            <span>
              This is the only time you&apos;ll see the full key. Save it in
              your secret store now.
            </span>
          </DialogDescription>
        </DialogHeader>
        {keyValue ? (
          <div className="flex items-center gap-2 rounded-md bg-muted p-3 font-mono text-xs">
            <span className="flex-1 break-all">{keyValue}</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                await navigator.clipboard.writeText(keyValue);
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
