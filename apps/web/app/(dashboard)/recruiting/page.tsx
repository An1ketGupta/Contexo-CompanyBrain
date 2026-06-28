"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  Briefcase,
  FileText,
  Hash,
  Plus,
  Search,
  Settings,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { NotionParentPicker } from "@/components/recruiting/notion-parent-picker";
import { SlackChannelPicker } from "@/components/recruiting/slack-channel-picker";

interface AtsPostingRow {
  platform: string;
  url: string | null;
  error: string | null;
}

interface RequisitionRow {
  id: string;
  role_request: string;
  status: string;
  ats_platform: string | null;
  ats_url: string | null;
  ats_postings: AtsPostingRow[];
  created_at: string;
  published_at: string | null;
}

interface ListResponse {
  requisitions: RequisitionRow[];
}

interface NotionParentStatus {
  connected: boolean;
  parent_id: string | null;
  parent_title: string | null;
  accessible: boolean;
  accessibility_error: string | null;
}

interface SlackChannelStatus {
  connected: boolean;
  channel_id: string | null;
  channel_name: string | null;
  accessible: boolean;
  accessibility_error: string | null;
}

type StatusKey = "all" | "draft" | "published" | "failed";

const PLATFORM_STYLE: Record<string, { label: string; bg: string; text: string }> = {
  greenhouse: { label: "Greenhouse", bg: "bg-emerald-100 dark:bg-emerald-500/20", text: "text-emerald-700 dark:text-emerald-300" },
  lever: { label: "Lever", bg: "bg-violet-100 dark:bg-violet-500/20", text: "text-violet-700 dark:text-violet-300" },
  ashby: { label: "Ashby", bg: "bg-orange-100 dark:bg-orange-500/20", text: "text-orange-700 dark:text-orange-300" },
  naukri: { label: "Naukri", bg: "bg-blue-100 dark:bg-blue-500/20", text: "text-blue-700 dark:text-blue-300" },
};

const STATUS_META: Record<
  Exclude<StatusKey, "all">,
  { label: string; dot: string; stripe: string; text: string; bg: string }
> = {
  draft: {
    label: "Draft",
    dot: "bg-zinc-400",
    stripe: "bg-zinc-300 dark:bg-zinc-600",
    text: "text-zinc-700 dark:text-zinc-300",
    bg: "bg-zinc-100 dark:bg-zinc-800",
  },
  published: {
    label: "Published",
    dot: "bg-emerald-500",
    stripe: "bg-emerald-500",
    text: "text-emerald-700 dark:text-emerald-300",
    bg: "bg-emerald-50 dark:bg-emerald-500/10",
  },
  failed: {
    label: "Failed",
    dot: "bg-red-500",
    stripe: "bg-red-500",
    text: "text-red-700 dark:text-red-300",
    bg: "bg-red-50 dark:bg-red-500/10",
  },
};

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

function relativeTime(iso: string): string {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString();
}

export default function RecruitingPage() {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [slackPickerOpen, setSlackPickerOpen] = useState(false);
  const [dismissedSetup, setDismissedSetup] = useState(false);
  const [dismissedSlackSetup, setDismissedSlackSetup] = useState(false);
  const [filter, setFilter] = useState<StatusKey>("all");
  const [query, setQuery] = useState("");

  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/recruiting/requisitions",
    fetcher,
    { revalidateOnFocus: false },
  );

  const { data: notionStatus, mutate: mutateNotionStatus } =
    useSWR<NotionParentStatus>(
      "/api/recruiting/notion-parent",
      fetcher,
      { revalidateOnFocus: false },
    );

  const { data: slackStatus, mutate: mutateSlackStatus } =
    useSWR<SlackChannelStatus>(
      "/api/recruiting/slack-channel",
      fetcher,
      { revalidateOnFocus: false },
    );

  const needsSetup =
    !!notionStatus &&
    (!notionStatus.parent_id || !notionStatus.accessible) &&
    !dismissedSetup;

  const needsSlackSetup =
    !!slackStatus &&
    (!slackStatus.channel_id || !slackStatus.accessible) &&
    !dismissedSlackSetup;

  const counts = useMemo(() => {
    const c = { all: 0, draft: 0, published: 0, failed: 0 } as Record<StatusKey, number>;
    for (const r of data?.requisitions ?? []) {
      c.all += 1;
      if (r.status === "draft" || r.status === "published" || r.status === "failed") {
        c[r.status as Exclude<StatusKey, "all">] += 1;
      }
    }
    return c;
  }, [data]);

  const visible = useMemo(() => {
    const rows = data?.requisitions ?? [];
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (filter !== "all" && r.status !== filter) return false;
      if (q && !r.role_request.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [data, filter, query]);

  const bothConfigured =
    notionStatus?.parent_id &&
    notionStatus.accessible &&
    slackStatus?.channel_id &&
    slackStatus.accessible;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Recruiting</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Generate JD variants from your KB, publish to your ATS, set up the
            hiring tracker.
          </p>
        </div>
        <Button asChild>
          <Link href="/recruiting/new">
            <Plus className="mr-2 h-4 w-4" /> New requisition
          </Link>
        </Button>
      </header>

      {needsSetup && (
        <NotionSetupCard
          status={notionStatus}
          onOpenPicker={() => setPickerOpen(true)}
          onDismiss={() => setDismissedSetup(true)}
        />
      )}
      {needsSlackSetup && (
        <SlackSetupCard
          status={slackStatus}
          onOpenPicker={() => setSlackPickerOpen(true)}
          onDismiss={() => setDismissedSlackSetup(true)}
        />
      )}

      {bothConfigured && (
        <div className="grid gap-2 sm:grid-cols-2">
          <StatusRow
            icon={FileText}
            label="Notion parent"
            value={notionStatus!.parent_title || "your Notion page"}
            onChange={() => setPickerOpen(true)}
            accent="notion"
          />
          <StatusRow
            icon={Hash}
            label="Slack channel"
            value={slackStatus!.channel_name || "your channel"}
            valuePrefix="#"
            onChange={() => setSlackPickerOpen(true)}
            accent="slack"
          />
        </div>
      )}

      {!bothConfigured && notionStatus?.parent_id && notionStatus.accessible && (
        <StatusRow
          icon={FileText}
          label="Notion parent"
          value={notionStatus.parent_title || "your Notion page"}
          onChange={() => setPickerOpen(true)}
          accent="notion"
        />
      )}
      {!bothConfigured && slackStatus?.channel_id && slackStatus.accessible && (
        <StatusRow
          icon={Hash}
          label="Slack channel"
          value={slackStatus.channel_name || "your channel"}
          valuePrefix="#"
          onChange={() => setSlackPickerOpen(true)}
          accent="slack"
        />
      )}

      <NotionParentPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        scope="org"
        onPicked={() => mutateNotionStatus()}
      />
      <SlackChannelPicker
        open={slackPickerOpen}
        onOpenChange={setSlackPickerOpen}
        scope="org"
        onPicked={() => mutateSlackStatus()}
      />

      {!isLoading && !error && (data?.requisitions?.length ?? 0) > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {(["all", "published", "draft", "failed"] as StatusKey[]).map((k) => {
              const active = filter === k;
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => setFilter(k)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
                    active
                      ? "border-foreground bg-foreground text-background"
                      : "border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground"
                  }`}
                >
                  <span className="capitalize">{k}</span>
                  <span
                    className={`rounded-full px-1.5 text-[10px] ${
                      active ? "bg-background/20" : "bg-muted text-foreground"
                    }`}
                  >
                    {counts[k]}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by role…"
              aria-label="Search requisitions"
              className="h-8 w-full rounded-full border border-border bg-background pl-8 pr-8 text-xs outline-none transition placeholder:text-muted-foreground focus:border-foreground/40"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-0.5 text-muted-foreground transition hover:bg-muted hover:text-foreground"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          Failed to load requisitions.
        </div>
      ) : !data?.requisitions?.length ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card/40 p-12 text-center">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted">
            <Briefcase className="h-5 w-5 text-muted-foreground" />
          </div>
          <div>
            <p className="font-medium">No requisitions yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Generate your first JD variant from your knowledge base.
            </p>
          </div>
          <Button asChild size="sm">
            <Link href="/recruiting/new">
              <Plus className="mr-1.5 h-3.5 w-3.5" /> New requisition
            </Link>
          </Button>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          {query.trim()
            ? `No requisitions match "${query.trim()}".`
            : `No ${filter} requisitions.`}
        </div>
      ) : (
        <ul className="space-y-2">
          {visible.map((r) => (
            <RequisitionItem key={r.id} row={r} />
          ))}
        </ul>
      )}
    </div>
  );
}

function StatusRow({
  icon: Icon,
  label,
  value,
  valuePrefix,
  onChange,
  accent,
}: {
  icon: typeof Hash;
  label: string;
  value: string;
  valuePrefix?: string;
  onChange: () => void;
  accent: "notion" | "slack";
}) {
  const accentClass =
    accent === "notion"
      ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
      : "bg-[#4A154B] text-white";

  return (
    <div className="group relative flex items-center gap-3 rounded-xl border border-border bg-card px-3 py-2.5 transition hover:border-foreground/25 hover:shadow-sm">
      <div
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${accentClass}`}
        aria-hidden
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground">
            {label}
          </span>
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            Connected
          </span>
        </div>
        <div className="mt-0.5 flex min-w-0 items-baseline gap-0.5 text-sm font-medium text-foreground">
          {valuePrefix && (
            <span className="text-muted-foreground">{valuePrefix}</span>
          )}
          <span className="truncate">{value}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onChange}
        aria-label={`Change ${label.toLowerCase()}`}
        className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted hover:text-foreground"
      >
        <Settings className="h-3 w-3" />
        Change
      </button>
    </div>
  );
}

function RequisitionItem({ row }: { row: RequisitionRow }) {
  const statusMeta =
    STATUS_META[row.status as Exclude<StatusKey, "all">] ?? STATUS_META.draft;
  const platforms = row.ats_postings?.length
    ? row.ats_postings.map((p) => p.platform)
    : row.ats_platform
      ? [row.ats_platform]
      : [];

  return (
    <li className="overflow-hidden rounded-lg border border-border bg-card transition hover:border-foreground/30 hover:bg-accent/40">
      <Link href={`/recruiting/${row.id}`} className="flex">
        <span
          aria-hidden
          className={`w-1 shrink-0 ${statusMeta.stripe}`}
        />
        <div className="flex flex-1 items-center justify-between gap-4 p-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-medium">{row.role_request}</h2>
            <p
              className="mt-0.5 text-xs text-muted-foreground"
              title={new Date(row.created_at).toLocaleString()}
            >
              Created {relativeTime(row.created_at)}
              {row.published_at && (
                <>
                  {" · "}
                  <span title={new Date(row.published_at).toLocaleString()}>
                    Published {relativeTime(row.published_at)}
                  </span>
                </>
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {platforms.map((p) => {
              const style = PLATFORM_STYLE[p];
              if (!style) {
                return (
                  <span
                    key={p}
                    className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[11px] font-medium capitalize text-muted-foreground"
                  >
                    {p}
                  </span>
                );
              }
              return (
                <span
                  key={p}
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${style.bg} ${style.text}`}
                >
                  <span
                    aria-hidden
                    className={`flex h-4 w-4 items-center justify-center rounded text-[9px] font-bold ${style.bg} ${style.text}`}
                  >
                    {style.label.charAt(0)}
                  </span>
                  {style.label}
                </span>
              );
            })}
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${statusMeta.bg} ${statusMeta.text}`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${statusMeta.dot}`} />
              {statusMeta.label}
            </span>
          </div>
        </div>
      </Link>
    </li>
  );
}

function SlackSetupCard({
  status,
  onOpenPicker,
  onDismiss,
}: {
  status: SlackChannelStatus;
  onOpenPicker: () => void;
  onDismiss: () => void;
}) {
  const variant: "not-connected" | "no-channel" | "channel-broken" =
    !status.connected
      ? "not-connected"
      : !status.channel_id
        ? "no-channel"
        : "channel-broken";

  const headline =
    variant === "not-connected"
      ? "Connect Slack to announce new requisitions"
      : variant === "no-channel"
        ? "Pick a Slack channel for recruiting announcements"
        : "Slack channel is no longer reachable";

  const body =
    variant === "not-connected"
      ? "Every requisition you publish drops a one-line announcement into one Slack channel. Pick that channel once — every publish after that posts automatically."
      : variant === "no-channel"
        ? "Choose the Slack channel where new openings should be announced. Set this once; every published requisition posts there."
        : `NirnayaIQ can no longer post to #${status.channel_name || "the configured channel"}. Re-invite the bot, or pick a different channel.`;

  const cta =
    variant === "channel-broken" ? "Pick a different channel" : "Set up Slack";

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4">
      {variant === "channel-broken" ? (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
      ) : (
        <Settings className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium">{headline}</p>
        <p className="mt-1 text-sm text-muted-foreground">{body}</p>
        {variant === "channel-broken" && status.accessibility_error && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            {status.accessibility_error}
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" onClick={onOpenPicker}>
            {cta}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Skip for now
          </Button>
        </div>
      </div>
    </div>
  );
}

function NotionSetupCard({
  status,
  onOpenPicker,
  onDismiss,
}: {
  status: NotionParentStatus;
  onOpenPicker: () => void;
  onDismiss: () => void;
}) {
  const variant: "not-connected" | "no-parent" | "parent-broken" =
    !status.connected
      ? "not-connected"
      : !status.parent_id
        ? "no-parent"
        : "parent-broken";

  const headline =
    variant === "not-connected"
      ? "Connect Notion to enable hiring trackers"
      : variant === "no-parent"
        ? "Pick a parent page for hiring trackers"
        : "Hiring tracker page is no longer accessible";

  const body =
    variant === "not-connected"
      ? "Every requisition you publish creates a child page under one Notion parent. Pick that parent once — every tracker after that nests automatically."
      : variant === "no-parent"
        ? "Choose the Notion page where all hiring trackers should live. You'll set this once; every published requisition will create a child page under it."
        : `NirnayaIQ can no longer write to "${status.parent_title || "the previous parent"}". Either re-share the page in Notion, or pick a different parent.`;

  const cta =
    variant === "parent-broken" ? "Pick a different parent" : "Set up Notion";

  return (
    <div className="flex items-start gap-3 rounded-lg border border-border bg-card p-4">
      {variant === "parent-broken" ? (
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
      ) : (
        <Settings className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium">{headline}</p>
        <p className="mt-1 text-sm text-muted-foreground">{body}</p>
        {variant === "parent-broken" && status.accessibility_error && (
          <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
            {status.accessibility_error}
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" onClick={onOpenPicker}>
            {cta}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Skip for now
          </Button>
        </div>
      </div>
    </div>
  );
}
