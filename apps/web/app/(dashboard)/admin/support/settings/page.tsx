"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { toast } from "sonner";
import { ArrowLeft, Loader2 } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface SupportSettings {
  enabled: boolean;
  mode: "shadow" | "assisted" | "autonomous";
  autonomous_categories: string[];
  sender_user_id: string | null;
  tone: string | null;
  escalation_channel_id: string | null;
  escalation_channel_name: string | null;
}

interface Sender {
  user_id: string;
  email_address: string;
}

const MODES: {
  value: SupportSettings["mode"];
  label: string;
  description: string;
}[] = [
  {
    value: "shadow",
    label: "Shadow",
    description:
      "The agent drafts silently so you can measure quality. Nothing is shown to reps as actionable, nothing is sent.",
  },
  {
    value: "assisted",
    label: "Assisted",
    description:
      "Every draft waits for a human to review, edit, and send. Recommended starting point.",
  },
  {
    value: "autonomous",
    label: "Autonomous",
    description:
      "Replies send themselves — but only for the categories you list below, and only when the draft is well-grounded.",
  },
];

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function SupportSettingsPage() {
  const { data, error, isLoading, mutate } = useSWR<SupportSettings>(
    "/api/admin/support/settings",
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: sendersData } = useSWR<{ senders: Sender[] }>(
    "/api/admin/support/settings/senders",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [form, setForm] = useState<SupportSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data && !form) setForm(data);
  }, [data, form]);

  const save = async (patch: Partial<SupportSettings>) => {
    if (!form) return;
    const next = { ...form, ...patch };
    setForm(next);
    setSaving(true);
    try {
      const res = await fetch("/api/admin/support/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `Save failed (${res.status})`);
      }
      await mutate();
      toast.success("Saved.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  if (isLoading || !form) {
    return (
      <div className="mx-auto w-full max-w-2xl space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto w-full max-w-2xl p-6">
        <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-600 dark:text-red-400">
          {error.message}
        </div>
      </div>
    );
  }

  const senders = sendersData?.senders ?? [];

  return (
    <div className="mx-auto w-full max-w-2xl space-y-5 p-6">
      <div>
        <Link
          href="/admin/support"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to queue
        </Link>
        <h1 className="mt-2 text-2xl font-extrabold tracking-tight">
          Support settings
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Controls how much the support agent is allowed to do on its own.
        </p>
      </div>

      <section className="rounded-2xl border border-border bg-card p-4">
        <label className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-bold">Enable the support agent</div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              When off, inbound support email is classified but no tickets or
              drafts are created.
            </p>
          </div>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => save({ enabled: e.target.checked })}
            className="mt-1 h-4 w-4 shrink-0 accent-[var(--brand)]"
          />
        </label>
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Trust mode
        </div>
        <div className="mt-3 space-y-2">
          {MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => save({ mode: m.value })}
              className={cn(
                "w-full rounded-xl border p-3 text-left transition-colors",
                form.mode === m.value
                  ? "border-brand bg-brand/5"
                  : "border-border hover:bg-muted",
              )}
            >
              <div className="text-sm font-bold">{m.label}</div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {m.description}
              </p>
            </button>
          ))}
        </div>
        <p className="mt-3 rounded-xl border border-border bg-muted px-3 py-2 text-[11px] text-muted-foreground">
          Regardless of mode, a reply is always held for a human when the
          customer sounds unhappy or when nothing in your knowledge base
          matched their question.
        </p>
      </section>

      {form.mode === "autonomous" && (
        <section className="rounded-2xl border border-border bg-card p-4">
          <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
            Auto-send categories
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Comma-separated topic slugs the agent may answer without review —
            e.g. <code>how-to, login, password-reset</code>. Keep this list to
            low-risk questions; billing and refunds are safer left to a human.
          </p>
          <Input
            defaultValue={form.autonomous_categories.join(", ")}
            onBlur={(e) =>
              save({
                autonomous_categories: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
            placeholder="how-to, login"
            className="mt-2"
          />
        </section>
      )}

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Sending mailbox
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Replies go out from a connected Gmail account with send permission.
          Without one, drafts are review-and-copy only.
        </p>
        {senders.length === 0 ? (
          <p className="mt-2 rounded-xl border border-amber/30 bg-amber-tint px-3 py-2 text-xs text-amber-ink">
            No mailbox with send permission connected.{" "}
            <Link href="/settings/integrations" className="underline">
              Connect Gmail
            </Link>{" "}
            and grant send access.
          </p>
        ) : (
          <select
            value={form.sender_user_id ?? ""}
            onChange={(e) => save({ sender_user_id: e.target.value || null })}
            className="mt-2 w-full rounded-md border border-input bg-card px-2.5 py-2 text-sm"
          >
            <option value="">None — draft only</option>
            {senders.map((s) => (
              <option key={s.user_id} value={s.user_id}>
                {s.email_address}
              </option>
            ))}
          </select>
        )}
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Tone
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Optional. How replies should sound, plus any sign-off you want used.
        </p>
        <Textarea
          defaultValue={form.tone ?? ""}
          onBlur={(e) => save({ tone: e.target.value })}
          rows={3}
          placeholder="Warm but concise. Sign off as 'The Acme Support Team'."
          className="mt-2 text-sm"
        />
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Escalation Slack channel
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Channel ID to ping when a ticket needs a human. Leave blank to skip
          notifications.
        </p>
        <Input
          defaultValue={form.escalation_channel_id ?? ""}
          onBlur={(e) => save({ escalation_channel_id: e.target.value || null })}
          placeholder="C0123456789"
          className="mt-2"
        />
      </section>

      {saving && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> Saving…
        </div>
      )}
    </div>
  );
}
