"use client";

import { useState } from "react";
import {
  AlertTriangle,
  BellRing,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import useSWR from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface DocRow {
  document_id: string;
  name: string | null;
  file_type: string | null;
  version_number: number | null;
  total: number;
  acknowledged: number;
  pending: number;
  dismissed: number;
  completion_pct: number;
  last_updated: string | null;
}

interface UserRow {
  user_id: string;
  display_name: string | null;
  email: string | null;
  total: number;
  acknowledged: number;
  pending: number;
  dismissed: number;
  last_acknowledged_at: string | null;
  pending_docs: { document_id: string; name: string | null }[];
}

interface ReportSummary {
  total_acks: number;
  acknowledged: number;
  pending: number;
  dismissed: number;
  policy_documents: number;
  users_with_pending: number;
  overall_completion_pct: number;
}

interface ReportResponse {
  summary: ReportSummary;
  by_document: DocRow[];
  by_user: UserRow[];
}

const fetcher = async (url: string) => {
  const res = await fetch(url, { credentials: "include" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

type Tab = "documents" | "users";

export default function AdminCompliancePage() {
  const { data, error, isLoading, mutate } = useSWR<ReportResponse>(
    "/api/admin/compliance/report",
    fetcher,
    { revalidateOnFocus: false },
  );
  const [tab, setTab] = useState<Tab>("documents");
  const [busyDoc, setBusyDoc] = useState<string | null>(null);

  async function repropagate(documentId: string) {
    setBusyDoc(documentId);
    try {
      const res = await fetch(
        `/api/admin/compliance/${documentId}/repropagate`,
        { method: "POST", credentials: "include" },
      );
      if (!res.ok) {
        toast.error("Couldn't queue re-propagation.");
        return;
      }
      toast.success("Re-propagation queued. Slack post + ack fan-out will run.");
      await mutate();
    } finally {
      setBusyDoc(null);
    }
  }

  async function remindNow(documentId: string) {
    setBusyDoc(documentId);
    try {
      const res = await fetch(
        `/api/admin/compliance/${documentId}/remind`,
        { method: "POST", credentials: "include" },
      );
      if (!res.ok) {
        toast.error("Couldn't send reminders.");
        return;
      }
      toast.success("Reminder emails queued.");
    } finally {
      setBusyDoc(null);
    }
  }

  if (error) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 md:px-8">
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          {error.message}
        </div>
      </div>
    );
  }

  const summary = data?.summary;
  const docs = data?.by_document ?? [];
  const users = data?.by_user ?? [];

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 md:px-8">
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-blue-600" />
            <h1 className="text-2xl font-semibold tracking-tight">
              Policy compliance
            </h1>
          </div>
          <p className="text-sm text-muted-foreground">
            Track acknowledgement status across every policy document. Trigger
            re-propagation or send reminder emails for pending items.
          </p>
        </div>
        <Button asChild variant="outline" size="sm">
          <a href="/api/admin/compliance/export" download>
            <Download className="mr-1.5 h-4 w-4" /> Export CSV
          </a>
        </Button>
      </div>

      {/* Summary tiles */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <SummaryTile
          icon={<ShieldCheck className="h-4 w-4 text-emerald-600" />}
          label="Overall completion"
          value={
            isLoading
              ? "…"
              : `${summary?.overall_completion_pct ?? 0}%`
          }
        />
        <SummaryTile
          icon={<FileText className="h-4 w-4 text-blue-600" />}
          label="Policy docs"
          value={isLoading ? "…" : String(summary?.policy_documents ?? 0)}
        />
        <SummaryTile
          icon={<AlertTriangle className="h-4 w-4 text-amber-600" />}
          label="Pending acks"
          value={isLoading ? "…" : String(summary?.pending ?? 0)}
        />
        <SummaryTile
          icon={<Users className="h-4 w-4 text-rose-600" />}
          label="Users with pending"
          value={isLoading ? "…" : String(summary?.users_with_pending ?? 0)}
        />
      </div>

      {/* Tab switcher */}
      <div className="mb-4 inline-flex rounded-md border bg-muted/40 p-1">
        <button
          className={cn(
            "rounded px-3 py-1.5 text-sm font-medium",
            tab === "documents"
              ? "bg-background shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
          onClick={() => setTab("documents")}
        >
          By document
        </button>
        <button
          className={cn(
            "rounded px-3 py-1.5 text-sm font-medium",
            tab === "users"
              ? "bg-background shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
          onClick={() => setTab("users")}
        >
          By employee
        </button>
      </div>

      {isLoading ? (
        <div className="overflow-hidden rounded-lg border border-border bg-card">
          <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-4 py-2.5">
            <Skeleton className="h-3 w-24" />
            <div className="ml-auto flex gap-4">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-16" />
            </div>
          </div>
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
            >
              <Skeleton className="h-4 w-4 rounded" />
              <div className="flex-1 space-y-1.5">
                <Skeleton
                  className="h-3.5"
                  style={{ width: `${40 + ((i * 11) % 35)}%` }}
                />
                <Skeleton className="h-2.5 w-1/4" />
              </div>
              <Skeleton className="h-5 w-20 rounded-full" />
              <Skeleton className="h-7 w-20 rounded-md" />
            </div>
          ))}
        </div>
      ) : tab === "documents" ? (
        <DocumentsTable
          docs={docs}
          busyDoc={busyDoc}
          onRepropagate={repropagate}
          onRemind={remindNow}
        />
      ) : (
        <UsersTable users={users} />
      )}

      <div className="mt-10 grid gap-6 md:grid-cols-2">
        <ComplianceConfigCard />
        <DigestCard />
      </div>
    </div>
  );
}

function ComplianceConfigCard() {
  const { data, mutate } = useSWR<{
    reminder_threshold_days: number;
    max_reminders: number;
    reminder_cadence_days: number;
    policy_channel_id: string | null;
    auto_propagate_policy_tag: boolean;
  }>("/api/admin/compliance/config", fetcher);
  const [form, setForm] = useState<{
    reminder_threshold_days: string;
    max_reminders: string;
    reminder_cadence_days: string;
    policy_channel_id: string;
    auto_propagate_policy_tag: boolean;
  } | null>(null);
  const [saving, setSaving] = useState(false);

  if (data && !form) {
    setForm({
      reminder_threshold_days: String(data.reminder_threshold_days),
      max_reminders: String(data.max_reminders),
      reminder_cadence_days: String(data.reminder_cadence_days),
      policy_channel_id: data.policy_channel_id ?? "",
      auto_propagate_policy_tag: data.auto_propagate_policy_tag,
    });
  }

  async function save() {
    if (!form) return;
    setSaving(true);
    try {
      const res = await fetch("/api/admin/compliance/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          reminder_threshold_days: Number(form.reminder_threshold_days),
          max_reminders: Number(form.max_reminders),
          reminder_cadence_days: Number(form.reminder_cadence_days),
          policy_channel_id: form.policy_channel_id || null,
          auto_propagate_policy_tag: form.auto_propagate_policy_tag,
        }),
      });
      if (!res.ok) {
        toast.error("Couldn't save compliance settings.");
        return;
      }
      toast.success("Compliance settings saved.");
      await mutate();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border bg-card p-5">
      <h3 className="mb-1 text-base font-medium">Reminder cadence</h3>
      <p className="mb-4 text-xs text-muted-foreground">
        How aggressively to chase pending acknowledgements. Defaults are
        sensible — only change if your team has different norms.
      </p>
      {!form ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      ) : (
        <div className="space-y-3">
          <Field label="First reminder after (days)">
            <input
              type="number"
              min={1}
              max={30}
              className="w-24 rounded-md border bg-background px-2 py-1 text-sm"
              value={form.reminder_threshold_days}
              onChange={(e) =>
                setForm({ ...form, reminder_threshold_days: e.target.value })
              }
            />
          </Field>
          <Field label="Max reminders per user/doc">
            <input
              type="number"
              min={0}
              max={10}
              className="w-24 rounded-md border bg-background px-2 py-1 text-sm"
              value={form.max_reminders}
              onChange={(e) => setForm({ ...form, max_reminders: e.target.value })}
            />
          </Field>
          <Field label="Days between reminders">
            <input
              type="number"
              min={1}
              max={14}
              className="w-24 rounded-md border bg-background px-2 py-1 text-sm"
              value={form.reminder_cadence_days}
              onChange={(e) =>
                setForm({ ...form, reminder_cadence_days: e.target.value })
              }
            />
          </Field>
          <Field label="Slack channel for policy posts">
            <input
              type="text"
              placeholder="C0123ABCDEF"
              className="w-48 rounded-md border bg-background px-2 py-1 font-mono text-sm"
              value={form.policy_channel_id}
              onChange={(e) =>
                setForm({ ...form, policy_channel_id: e.target.value })
              }
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.auto_propagate_policy_tag}
              onChange={(e) =>
                setForm({
                  ...form,
                  auto_propagate_policy_tag: e.target.checked,
                })
              }
            />
            Auto-propagate documents tagged{" "}
            <code className="rounded bg-muted px-1 py-0.5 text-xs">policy</code>
          </label>
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            Save settings
          </Button>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-sm text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function DigestCard() {
  const { data } = useSWR<{
    stats: {
      time_saved_hours: number;
      query_count: number;
      knowledge_gaps_count: number;
      ack_pending_count: number;
      new_document_count: number;
    };
  }>("/api/admin/weekly-digest/preview", fetcher);
  const [sending, setSending] = useState(false);

  async function sendNow() {
    setSending(true);
    try {
      const res = await fetch("/api/admin/weekly-digest/send-now", {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        toast.error("Couldn't queue digest.");
        return;
      }
      toast.success("Digest queued. Check your inbox in a minute.");
    } finally {
      setSending(false);
    }
  }

  const s = data?.stats;
  return (
    <div className="rounded-lg border bg-card p-5">
      <h3 className="mb-1 text-base font-medium">Weekly digest preview</h3>
      <p className="mb-4 text-xs text-muted-foreground">
        Every Monday at 09:00 UTC each admin gets an email like this. Send
        yourself a test below.
      </p>
      {!s ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      ) : (
        <div className="mb-4 grid grid-cols-2 gap-3 text-sm">
          <Mini label="Time saved" value={`${s.time_saved_hours} hr`} />
          <Mini label="Queries" value={String(s.query_count)} />
          <Mini label="New docs" value={String(s.new_document_count)} />
          <Mini label="Gaps" value={String(s.knowledge_gaps_count)} />
          <Mini label="Acks pending" value={String(s.ack_pending_count)} />
        </div>
      )}
      <Button size="sm" onClick={sendNow} disabled={sending}>
        {sending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
        Send me a test digest
      </Button>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-muted/40 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold">{value}</div>
    </div>
  );
}

function SummaryTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
        {icon}
        <span>{label}</span>
      </div>
      <div className="text-2xl font-semibold">{value}</div>
    </div>
  );
}

function DocumentsTable({
  docs,
  busyDoc,
  onRepropagate,
  onRemind,
}: {
  docs: DocRow[];
  busyDoc: string | null;
  onRepropagate: (id: string) => void;
  onRemind: (id: string) => void;
}) {
  if (docs.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-10 text-center text-sm text-muted-foreground">
        No policy documents propagated yet. Tag any document with{" "}
        <code className="rounded bg-muted px-1.5 py-0.5">policy</code> or mark
        it &ldquo;requires acknowledgement&rdquo; to start.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left">Document</th>
            <th className="px-3 py-3 text-left">Version</th>
            <th className="px-3 py-3 text-right">Acked</th>
            <th className="px-3 py-3 text-right">Pending</th>
            <th className="px-3 py-3 text-right">Completion</th>
            <th className="px-3 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.document_id} className="border-t">
              <td className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-muted-foreground" />
                  <span className="font-medium">
                    {d.name ?? "Untitled"}
                  </span>
                </div>
              </td>
              <td className="px-3 py-3">
                {d.version_number != null ? `v${d.version_number}` : "—"}
              </td>
              <td className="px-3 py-3 text-right">{d.acknowledged}</td>
              <td className="px-3 py-3 text-right">
                {d.pending > 0 ? (
                  <Badge variant="accent">{d.pending}</Badge>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </td>
              <td className="px-3 py-3 text-right">
                <CompletionBar pct={d.completion_pct} />
              </td>
              <td className="px-3 py-3 text-right">
                <div className="flex justify-end gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busyDoc === d.document_id}
                    onClick={() => onRemind(d.document_id)}
                    title="Send reminder emails now"
                  >
                    <BellRing className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busyDoc === d.document_id}
                    onClick={() => onRepropagate(d.document_id)}
                    title="Re-run propagation agent"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsersTable({ users }: { users: UserRow[] }) {
  if (users.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-10 text-center text-sm text-muted-foreground">
        No users in this org have been assigned acknowledgements yet.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left">Employee</th>
            <th className="px-3 py-3 text-right">Acked</th>
            <th className="px-3 py-3 text-right">Pending</th>
            <th className="px-3 py-3 text-right">Last acknowledged</th>
            <th className="px-3 py-3 text-left">Pending docs</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.user_id} className="border-t">
              <td className="px-4 py-3">
                <div className="font-medium">
                  {u.display_name ??
                    (u.email ? u.email.split("@")[0] : "Unknown")}
                </div>
                {u.email && (
                  <div className="text-xs text-muted-foreground">{u.email}</div>
                )}
              </td>
              <td className="px-3 py-3 text-right">{u.acknowledged}</td>
              <td className="px-3 py-3 text-right">
                {u.pending > 0 ? (
                  <Badge variant="accent">{u.pending}</Badge>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </td>
              <td className="px-3 py-3 text-right text-xs text-muted-foreground">
                {u.last_acknowledged_at
                  ? new Date(u.last_acknowledged_at).toLocaleDateString()
                  : "—"}
              </td>
              <td className="px-3 py-3 text-xs">
                {u.pending_docs.length === 0 ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <span className="truncate">
                    {u.pending_docs
                      .map((d) => d.name ?? "Untitled")
                      .slice(0, 3)
                      .join(", ")}
                    {u.pending_docs.length > 3
                      ? ` +${u.pending_docs.length - 3} more`
                      : ""}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CompletionBar({ pct }: { pct: number }) {
  const tone =
    pct >= 90
      ? "bg-emerald-500"
      : pct >= 60
        ? "bg-blue-500"
        : pct >= 30
          ? "bg-amber-500"
          : "bg-rose-500";
  return (
    <div className="ml-auto flex w-32 items-center gap-2">
      <div className="h-1.5 flex-1 rounded-full bg-muted">
        <div
          className={cn("h-full rounded-full", tone)}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
      <span className="w-10 text-right text-xs tabular-nums text-muted-foreground">
        {pct}%
      </span>
    </div>
  );
}
