"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Calendar, Hash, Loader2, Lock, Send, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";

interface Announcement {
  id: string;
  request_text: string;
  email_subject: string | null;
  email_body: string | null;
  slack_body: string | null;
  notion_title: string | null;
  notion_body: string | null;
  status: string;
  scheduled_for: string | null;
  recipients: string[];
  slack_channel_id: string | null;
  notion_parent_page_id: string | null;
  sent_at: string | null;
  email_sent_count: number;
  slack_ts: string | null;
  notion_page_url: string | null;
  error_message: string | null;
  created_at: string;
}

interface Response {
  announcement: Announcement;
}

interface SlackChannel {
  id: string;
  name: string;
  is_private?: boolean;
}

interface NotionPage {
  id: string;
  title: string;
  url?: string | null;
}

interface OrgMember {
  id: string;
  email: string;
  display_name: string | null;
}

type TabId = "email" | "slack" | "notion";

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

function toLocalIso(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export default function AnnouncementDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  const { data, error, isLoading, mutate } = useSWR<Response>(
    id ? `/api/admin/announcements/${id}` : null,
    fetcher,
    { revalidateOnFocus: false },
  );

  const [tab, setTab] = useState<TabId>("email");

  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [slackBody, setSlackBody] = useState("");
  const [notionTitle, setNotionTitle] = useState("");
  const [notionBody, setNotionBody] = useState("");

  const [sendToAll, setSendToAll] = useState(false);
  const [recipients, setRecipients] = useState("");
  const [slackChannel, setSlackChannel] = useState("");
  const [notionParent, setNotionParent] = useState("");
  const [whenLocal, setWhenLocal] = useState("");

  const [working, setWorking] = useState(false);

  // ── Slack channels ────────────────────────────────────────────────────
  const { data: slackData, error: slackError, isLoading: slackLoading } = useSWR<
    { channels: SlackChannel[] }
  >("/api/integrations/slack/channels", fetcher, { revalidateOnFocus: false });
  const slackChannels = slackData?.channels ?? [];

  // ── Notion write-target pages ─────────────────────────────────────────
  const { data: notionData, error: notionError, isLoading: notionLoading } = useSWR<
    { pages: NotionPage[] }
  >("/api/integrations/notion/write-targets", fetcher, { revalidateOnFocus: false });
  const notionPages = notionData?.pages ?? [];

  useEffect(() => {
    if (!data) return;
    const a = data.announcement;
    setEmailSubject(a.email_subject ?? "");
    setEmailBody(a.email_body ?? "");
    setSlackBody(a.slack_body ?? "");
    setNotionTitle(a.notion_title ?? "");
    setNotionBody(a.notion_body ?? "");
    setRecipients((a.recipients ?? []).join(", "));
    setSlackChannel(a.slack_channel_id ?? "");
    setNotionParent(a.notion_parent_page_id ?? "");
    if (a.scheduled_for) {
      setWhenLocal(toLocalIso(new Date(a.scheduled_for)));
    } else if (!whenLocal) {
      // Default to 1 hour from now.
      const d = new Date(Date.now() + 60 * 60 * 1000);
      d.setSeconds(0, 0);
      setWhenLocal(toLocalIso(d));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-4 p-6">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="mx-auto max-w-4xl p-6 text-sm text-destructive">
        {(error as Error)?.message ?? "Not found."}
      </div>
    );
  }

  const a = data.announcement;
  const isDraft = a.status === "draft";

  const saveDraft = async () => {
    setWorking(true);
    try {
      const res = await fetch(`/api/admin/announcements/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email_subject: emailSubject,
          email_body: emailBody,
          slack_body: slackBody,
          notion_title: notionTitle,
          notion_body: notionBody,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.message || `Failed (${res.status})`);
      }
      toast.success("Saved.");
      mutate();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const schedule = async () => {
    if (!whenLocal) {
      toast.error("Pick a send time.");
      return;
    }
    setWorking(true);
    try {
      // Save edits first.
      await fetch(`/api/admin/announcements/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email_subject: emailSubject,
          email_body: emailBody,
          slack_body: slackBody,
          notion_title: notionTitle,
          notion_body: notionBody,
        }),
      });

      let recipientList: string[];
      if (sendToAll) {
        // Fetch all org members' emails at schedule time.
        const membersRes = await fetch("/api/organizations/members");
        if (!membersRes.ok) throw new Error("Couldn't load employee list.");
        const membersData: { members: OrgMember[] } = await membersRes.json();
        recipientList = membersData.members
          .map((m) => m.email)
          .filter(Boolean);
        if (recipientList.length === 0) {
          throw new Error("No employees found with email addresses.");
        }
      } else {
        recipientList = recipients
          .split(/[,\n]/)
          .map((s) => s.trim())
          .filter(Boolean);
      }

      const scheduledFor = new Date(whenLocal).toISOString();

      const res = await fetch(`/api/admin/announcements/${id}/schedule`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scheduled_for: scheduledFor,
          recipients: recipientList,
          slack_channel_id: (slackChannel && slackChannel !== "__none__") ? slackChannel : null,
          notion_parent_page_id: (notionParent && notionParent !== "__none__") ? notionParent : null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.message || `Failed (${res.status})`);
      }
      toast.success("Announcement scheduled.");
      mutate();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setWorking(false);
    }
  };

  const cancel = async () => {
    if (!confirm("Cancel this announcement? Scheduled channels won't fire.")) return;
    setWorking(true);
    try {
      const res = await fetch(`/api/admin/announcements/${id}/cancel`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      mutate();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-extrabold tracking-tight">
            {emailSubject || "Untitled announcement"}
          </h1>
        </div>
        <Badge>{a.status}</Badge>
      </header>

      <div className="flex w-fit gap-1 rounded-xl bg-muted p-1">
        <TabButton active={tab === "email"} onClick={() => setTab("email")} label="Email" />
        <TabButton active={tab === "slack"} onClick={() => setTab("slack")} label="Slack" />
        <TabButton active={tab === "notion"} onClick={() => setTab("notion")} label="Notion" />
      </div>

      {tab === "email" && (
        <section className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="email-subject">Subject</Label>
            <Input
              id="email-subject"
              value={emailSubject}
              onChange={(e) => setEmailSubject(e.target.value)}
              disabled={!isDraft}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="email-body">Body (Markdown)</Label>
            <Textarea
              id="email-body"
              value={emailBody}
              onChange={(e) => setEmailBody(e.target.value)}
              rows={16}
              className="font-mono text-sm"
              disabled={!isDraft}
            />
          </div>
        </section>
      )}
      {tab === "slack" && (
        <section className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="slack-body">Slack post (mrkdwn)</Label>
            <Textarea
              id="slack-body"
              value={slackBody}
              onChange={(e) => setSlackBody(e.target.value)}
              rows={14}
              className="font-mono text-sm"
              disabled={!isDraft}
            />
          </div>
        </section>
      )}
      {tab === "notion" && (
        <section className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="notion-title">Page title</Label>
            <Input
              id="notion-title"
              value={notionTitle}
              onChange={(e) => setNotionTitle(e.target.value)}
              disabled={!isDraft}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="notion-body">Body (Markdown)</Label>
            <Textarea
              id="notion-body"
              value={notionBody}
              onChange={(e) => setNotionBody(e.target.value)}
              rows={14}
              className="font-mono text-sm"
              disabled={!isDraft}
            />
          </div>
        </section>
      )}

      <section className="rounded-2xl border border-border bg-card p-5">
        <h2 className="text-[15px] font-bold">Channels & schedule</h2>
        <div className="mt-3 space-y-3">
          {/* ── Email recipients ─────────────────────────────────── */}
          <div className="space-y-1.5">
            <Label>Email recipients</Label>
            <label className="flex items-center gap-2 py-1">
              <Checkbox
                checked={sendToAll}
                onCheckedChange={(v) => {
                  setSendToAll(v);
                  if (v) setRecipients("");
                }}
                disabled={!isDraft}
                aria-label="Send to all employees"
              />
              <span className="text-sm">Send to all employees</span>
            </label>
            {!sendToAll && (
              <>
                <Textarea
                  id="recipients"
                  value={recipients}
                  onChange={(e) => setRecipients(e.target.value)}
                  placeholder="alice@acme.com, bob@acme.com"
                  rows={2}
                  disabled={!isDraft}
                />
                <p className="text-xs text-muted-foreground">
                  Comma-separated.
                </p>
              </>
            )}
            {sendToAll && (
              <p className="text-xs text-muted-foreground">
                All employees in your organisation will receive this email.
              </p>
            )}
          </div>

          {/* ── Slack & Notion pickers ───────────────────────────── */}
          <div className="grid gap-3 sm:grid-cols-2">
            {/* Slack channel picker */}
            <div className="space-y-1.5">
              <Label htmlFor="slack-channel">Slack channel</Label>
              {slackError ? (
                <>
                  <div className="flex items-start gap-2 rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                    <span>
                      Slack isn&apos;t connected.{" "}
                      <a className="underline" href="/settings/integrations" target="_blank" rel="noreferrer">
                        Connect in Settings
                      </a>
                      , or paste a channel ID below.
                    </span>
                  </div>
                  <Input
                    id="slack-channel"
                    value={slackChannel}
                    onChange={(e) => setSlackChannel(e.target.value)}
                    placeholder="C0123ABCD"
                    className="font-mono text-xs"
                    disabled={!isDraft}
                  />
                </>
              ) : (
                <Select
                  value={slackChannel}
                  onValueChange={setSlackChannel}
                  disabled={!isDraft}
                >
                  <SelectTrigger id="slack-channel">
                    <SelectValue placeholder={slackLoading ? "Loading channels…" : "Pick a channel"} />
                  </SelectTrigger>
                  <SelectContent>
                    {/* Allow clearing selection */}
                    <SelectItem value="__none__">
                      <span className="text-muted-foreground">None (skip Slack)</span>
                    </SelectItem>
                    {slackChannels.length === 0 && !slackLoading ? (
                      <div className="px-2 py-3 text-center text-xs text-muted-foreground">
                        No channels found. Invite the bot to channels and refresh.
                      </div>
                    ) : (
                      slackChannels.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          <span className="inline-flex items-center gap-1.5">
                            {c.is_private ? <Lock className="size-3" /> : <Hash className="size-3" />}
                            {c.name}
                          </span>
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              )}
            </div>

            {/* Notion parent page picker */}
            <div className="space-y-1.5">
              <Label htmlFor="notion-parent">Notion parent page</Label>
              {notionError ? (
                <>
                  <div className="flex items-start gap-2 rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                    <span>
                      Notion isn&apos;t connected.{" "}
                      <a className="underline" href="/settings/integrations" target="_blank" rel="noreferrer">
                        Connect in Settings
                      </a>
                      , or paste a page ID below.
                    </span>
                  </div>
                  <Input
                    id="notion-parent"
                    value={notionParent}
                    onChange={(e) => setNotionParent(e.target.value)}
                    placeholder="32-char id"
                    className="font-mono text-xs"
                    disabled={!isDraft}
                  />
                </>
              ) : (
                <Select
                  value={notionParent}
                  onValueChange={setNotionParent}
                  disabled={!isDraft}
                >
                  <SelectTrigger id="notion-parent">
                    <SelectValue placeholder={notionLoading ? "Loading pages…" : "Pick a parent page"} />
                  </SelectTrigger>
                  <SelectContent>
                    {/* Allow clearing selection */}
                    <SelectItem value="__none__">
                      <span className="text-muted-foreground">None (skip Notion)</span>
                    </SelectItem>
                    {notionPages.length === 0 && !notionLoading ? (
                      <div className="px-2 py-3 text-center text-xs text-muted-foreground">
                        No pages found. Share pages with NirnayaIQ in Notion.
                      </div>
                    ) : (
                      notionPages.map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          <span className="truncate">{p.title || p.id}</span>
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              )}
            </div>
          </div>

          {/* ── Send time ────────────────────────────────────────── */}
          <div className="space-y-1.5">
            <Label htmlFor="when">Send at</Label>
            <Input
              id="when"
              type="datetime-local"
              value={whenLocal}
              onChange={(e) => setWhenLocal(e.target.value)}
              disabled={!isDraft}
            />
          </div>
        </div>
      </section>

      {a.status !== "draft" && (
        <section className="rounded-2xl border border-border bg-muted/50 p-4 text-xs text-muted-foreground">
          {a.status === "scheduled" && a.scheduled_for && (
            <p>Will send at {new Date(a.scheduled_for).toLocaleString()}.</p>
          )}
          {a.status === "sent" && (
            <ul className="space-y-1">
              {a.email_sent_count > 0 && <li>📧 Sent to {a.email_sent_count} recipient(s).</li>}
              {a.slack_ts && <li>💬 Slack posted ({a.slack_ts}).</li>}
              {a.notion_page_url && (
                <li>
                  📝{" "}
                  <a href={a.notion_page_url} target="_blank" rel="noreferrer" className="underline">
                    Notion page
                  </a>
                </li>
              )}
            </ul>
          )}
          {a.error_message && <p className="text-destructive-ink">{a.error_message}</p>}
        </section>
      )}

      <div className="flex justify-end gap-2">
        {(a.status === "draft" || a.status === "scheduled") && (
          <Button variant="outline" onClick={cancel} disabled={working} className="gap-2 rounded-full">
            <X className="size-4" />
            Cancel
          </Button>
        )}
        {isDraft && (
          <>
            <Button variant="outline" onClick={saveDraft} disabled={working} className="rounded-full">
              Save draft
            </Button>
            <Button onClick={schedule} disabled={working} className="gap-2 rounded-full">
              {working ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Calendar className="size-4" />
              )}
              {working ? "Scheduling…" : "Approve & schedule"}
            </Button>
          </>
        )}
        <Button variant="ghost" onClick={() => router.push("/admin/announcements")} className="rounded-full">
          Back
        </Button>
      </div>
    </div>
  );
}

function TabButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-lg px-3 py-1.5 text-sm font-bold transition-colors " +
        (active
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground")
      }
    >
      {label}
    </button>
  );
}
