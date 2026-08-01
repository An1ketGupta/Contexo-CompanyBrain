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
import type { SalesSettings, SalesTrustMode } from "@/lib/types";

interface Sender {
  user_id: string;
  email_address: string;
}

const MODES: { value: SalesTrustMode; label: string; description: string }[] = [
  {
    value: "shadow",
    label: "Shadow",
    description:
      "The agent drafts silently so you can judge quality. Nothing reaches the review queue, nothing is sent.",
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
      "Follow-up nudges can send themselves when they're well-grounded. Cold first emails and anything touching price still always wait for you.",
  },
];

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function SalesSettingsPage() {
  const { data, error, isLoading, mutate } = useSWR<SalesSettings>(
    "/api/admin/sales/settings",
    fetcher,
    { revalidateOnFocus: false },
  );
  const { data: sendersData } = useSWR<{ senders: Sender[] }>(
    "/api/admin/sales/settings/senders",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [form, setForm] = useState<SalesSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data && !form) setForm(data);
  }, [data, form]);

  const save = async (patch: Partial<SalesSettings>) => {
    if (!form) return;
    setForm({ ...form, ...patch });
    setSaving(true);
    try {
      const res = await fetch("/api/admin/sales/settings", {
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
          href="/admin/sales"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back to leads
        </Link>
        <h1 className="mt-2 text-2xl font-extrabold tracking-tight">
          Sales settings
        </h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Controls how much the sales agent is allowed to do on its own.
        </p>
      </div>

      <section className="rounded-2xl border border-border bg-card p-4">
        <label className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-bold">Enable the sales agent</div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              When off, imported leads sit untouched — no drafts, no follow-ups.
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
          Regardless of mode, three things always wait for a human: the first
          cold email to a lead, any draft that mentions price or contract terms,
          and any draft nothing in your knowledge base supported.
        </p>
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Sending mailbox
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Outbound mail goes from a teammate&apos;s own Gmail, so the prospect
          sees a real person and replies land in that person&apos;s inbox. With
          none set, drafts can still be reviewed and copied out manually.
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
          Follow-up cadence
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          How long to wait for a reply before nudging, and when to stop. A lead
          that never answers is marked lost after the last nudge rather than
          chased forever.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <label className="block">
            <span className="text-xs font-bold">Wait (days)</span>
            <Input
              type="number"
              min={1}
              max={60}
              defaultValue={form.follow_up_delay_days}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (v >= 1 && v <= 60 && v !== form.follow_up_delay_days) {
                  save({ follow_up_delay_days: v });
                }
              }}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-xs font-bold">Max follow-ups</span>
            <Input
              type="number"
              min={0}
              max={10}
              defaultValue={form.max_follow_ups}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (v >= 0 && v <= 10 && v !== form.max_follow_ups) {
                  save({ max_follow_ups: v });
                }
              }}
              className="mt-1"
            />
          </label>
          <label className="block">
            <span className="text-xs font-bold">Daily cap</span>
            <Input
              type="number"
              min={1}
              max={500}
              defaultValue={form.daily_send_cap}
              onBlur={(e) => {
                const v = Number(e.target.value);
                if (v >= 1 && v <= 500 && v !== form.daily_send_cap) {
                  save({ daily_send_cap: v });
                }
              }}
              className="mt-1"
            />
          </label>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          The daily cap limits how many leads the agent picks up each morning —
          it protects both your LLM spend and your sending domain&apos;s
          reputation from a very large import.
        </p>
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Tone
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Optional. How outreach should sound, plus any sign-off you want used.
        </p>
        <Textarea
          defaultValue={form.tone ?? ""}
          onBlur={(e) => save({ tone: e.target.value })}
          rows={3}
          placeholder="Direct and friendly, no jargon. Sign off as 'Priya from Acme'."
          className="mt-2 text-sm"
        />
      </section>

      <section className="rounded-2xl border border-border bg-card p-4">
        <div className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Review Slack channel
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Channel ID to ping when a draft needs a human. Leave blank to skip
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
