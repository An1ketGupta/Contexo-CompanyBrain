"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { AlertTriangle, CheckCircle2, Plus, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { NotionParentPicker } from "@/components/recruiting/notion-parent-picker";

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

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  published: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
};

export default function RecruitingPage() {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dismissedSetup, setDismissedSetup] = useState(false);

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

  // Show the empty-state card when:
  //   - we know Notion's state (not still loading)
  //   - no default parent is configured, OR the configured parent is no
  //     longer accessible (un-shared, deleted, etc.)
  //   - the user hasn't dismissed it for this session
  const needsSetup =
    !!notionStatus &&
    (!notionStatus.parent_id || !notionStatus.accessible) &&
    !dismissedSetup;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Recruiting</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Generate JD variants from your KB, publish to your ATS, set up the hiring tracker.
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

      {notionStatus?.parent_id && notionStatus.accessible && (
        <div className="flex items-center justify-between rounded border border-border bg-muted/30 px-3 py-2 text-xs">
          <span className="flex items-center gap-2 text-muted-foreground">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
            Hiring trackers nest under{" "}
            <span className="font-medium text-foreground">
              {notionStatus.parent_title || "your Notion page"}
            </span>
          </span>
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
          >
            <Settings className="h-3 w-3" />
            Change
          </button>
        </div>
      )}

      <NotionParentPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        scope="org"
        onPicked={() => mutateNotionStatus()}
      />

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          Failed to load requisitions.
        </div>
      ) : !data?.requisitions?.length ? (
        <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">
          No requisitions yet. Click <span className="font-medium">New requisition</span> to start.
        </div>
      ) : (
        <ul className="space-y-3">
          {data.requisitions.map((r) => (
            <li
              key={r.id}
              className="rounded-lg border border-border bg-card p-4 transition-colors hover:border-foreground/30 hover:bg-accent/40"
            >
              <Link href={`/recruiting/${r.id}`} className="block">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate font-medium">{r.role_request}</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {new Date(r.created_at).toLocaleString()}
                      {r.published_at && (
                        <>
                          {" · "}published {new Date(r.published_at).toLocaleDateString()}
                        </>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {(r.ats_postings?.length
                      ? r.ats_postings.map((p) => p.platform)
                      : r.ats_platform
                        ? [r.ats_platform]
                        : []
                    ).map((p) => (
                      <Badge key={p} variant="outline">
                        {p}
                      </Badge>
                    ))}
                    <Badge className={STATUS_COLORS[r.status] ?? ""}>{r.status}</Badge>
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
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
  // Three distinct empty-state messages so the user knows exactly what's
  // wrong and what to do about it.
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
