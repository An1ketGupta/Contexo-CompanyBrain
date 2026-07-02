"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  AlertTriangle,
  Clock,
  ExternalLink,
  FileWarning,
  GitMerge,
  Link2Off,
  Loader2,
  PlayCircle,
  Settings2,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface OutdatedDoc {
  id: string;
  name: string | null;
  updated_at: string | null;
  source: string | null;
  age_days: number | null;
}

interface BrokenLink {
  document_id: string;
  document_name: string | null;
  url: string;
  status_code: number | null;
  reason: string;
}

interface MergeMember {
  id: string;
  name: string | null;
}

interface MergeCluster {
  members: MergeMember[];
  size: number;
  avg_similarity: number;
}

interface KnowledgeGap {
  topic: string;
  count: number;
  sample_queries: string[];
  has_pending_draft: boolean;
  synthetic?: boolean;
}

interface DraftedStub {
  draft_id: string;
  topic: string;
  gap_count: number;
}

interface ReportPayload {
  outdated_documents?: OutdatedDoc[];
  broken_links?: BrokenLink[];
  merge_clusters?: MergeCluster[];
  knowledge_gaps?: KnowledgeGap[];
  drafted_stubs?: DraftedStub[];
  counts?: Record<string, number>;
  config_snapshot?: {
    outdated_days: number;
    merge_threshold: number;
    broken_link_check: boolean;
  };
}

interface Report {
  id: string;
  status: "pending" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
  triggered_by: string | null;
  payload: ReportPayload;
  error_message: string | null;
}

interface LatestResponse {
  report: Report | null;
}

interface CuratorSettings {
  curator_enabled: boolean;
  curator_outdated_days: number;
  curator_check_broken_links: boolean;
  curator_merge_threshold: number;
}

const jsonFetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function KnowledgeHealthPage() {
  const [scanning, setScanning] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const {
    data: latest,
    error: latestError,
    isLoading: latestLoading,
    mutate: mutateLatest,
  } = useSWR<LatestResponse>("/api/admin/knowledge-health/latest", jsonFetcher, {
    revalidateOnFocus: false,
    // While a scan is pending, poll every 5s so the dashboard reflects the
    // result as soon as the worker finishes. SWR stops polling automatically
    // once status flips to 'completed' or 'failed'.
    refreshInterval: (data) => (data?.report?.status === "pending" ? 5000 : 0),
  });

  const {
    data: settings,
    mutate: mutateSettings,
  } = useSWR<CuratorSettings>("/api/admin/knowledge-health/settings", jsonFetcher, {
    revalidateOnFocus: false,
  });

  const runScan = async () => {
    setScanning(true);
    try {
      const res = await fetch("/api/admin/knowledge-health/scan", { method: "POST" });
      if (!res.ok && res.status !== 202) throw new Error(`Failed (${res.status})`);
      toast.success("Scan queued. The report will appear here when it completes.");
      mutateLatest();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setScanning(false);
    }
  };

  const updateSettings = async (patch: Partial<CuratorSettings>) => {
    try {
      const res = await fetch("/api/admin/knowledge-health/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const next = (await res.json()) as CuratorSettings;
      mutateSettings(next, false);
      toast.success("Settings updated.");
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight">Knowledge health</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Weekly autonomous scan of your knowledge base. Surfaces outdated docs,
            broken links, merge candidates, and unanswered queries — with AI-drafted
            stubs ready for review.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowSettings((v) => !v)}
            className="gap-2 rounded-full"
          >
            <Settings2 className="size-4" />
            Settings
          </Button>
          <Button onClick={runScan} disabled={scanning} className="gap-2 rounded-full">
            {scanning ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <PlayCircle className="size-4" />
            )}
            {scanning ? "Queuing…" : "Run scan now"}
          </Button>
        </div>
      </header>

      {showSettings && settings && (
        <SettingsPanel settings={settings} onUpdate={updateSettings} />
      )}

      {latestError ? (
        <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive-ink">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(latestError as Error).message}</span>
        </div>
      ) : latestLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : !latest?.report ? (
        <EmptyState onRun={runScan} />
      ) : (
        <ReportView report={latest.report} />
      )}
    </div>
  );
}

function SettingsPanel({
  settings,
  onUpdate,
}: {
  settings: CuratorSettings;
  onUpdate: (patch: Partial<CuratorSettings>) => Promise<void>;
}) {
  const [days, setDays] = useState(settings.curator_outdated_days);
  const [threshold, setThreshold] = useState(settings.curator_merge_threshold);

  return (
    <section className="rounded-2xl border border-border bg-card p-5">
      <h2 className="text-[15px] font-bold">Curator settings</h2>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-muted/50 p-3">
          <div className="space-y-0.5">
            <Label className="text-sm font-medium">Weekly scan enabled</Label>
            <p className="text-xs text-muted-foreground">Runs Mondays 08:00 UTC.</p>
          </div>
          <Checkbox
            checked={settings.curator_enabled}
            onCheckedChange={(v) => onUpdate({ curator_enabled: Boolean(v) })}
          />
        </div>
        <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-muted/50 p-3">
          <div className="space-y-0.5">
            <Label className="text-sm font-medium">Check broken links</Label>
            <p className="text-xs text-muted-foreground">
              HEAD-probe URLs in document summaries.
            </p>
          </div>
          <Checkbox
            checked={settings.curator_check_broken_links}
            onCheckedChange={(v) =>
              onUpdate({ curator_check_broken_links: Boolean(v) })
            }
          />
        </div>
        <div className="space-y-1.5 rounded-xl border border-border bg-muted/50 p-3">
          <Label htmlFor="outdated-days" className="text-sm font-medium">
            Outdated cutoff (days)
          </Label>
          <div className="flex items-center gap-2">
            <Input
              id="outdated-days"
              type="number"
              min={30}
              max={3650}
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              onBlur={() =>
                days !== settings.curator_outdated_days &&
                onUpdate({ curator_outdated_days: days })
              }
              className="max-w-32"
            />
            <span className="text-xs text-muted-foreground">
              ≈ {(days / 30).toFixed(0)} months
            </span>
          </div>
        </div>
        <div className="space-y-1.5 rounded-xl border border-border bg-muted/50 p-3">
          <Label htmlFor="merge-threshold" className="text-sm font-medium">
            Merge similarity threshold
          </Label>
          <div className="flex items-center gap-2">
            <Input
              id="merge-threshold"
              type="number"
              min={0.5}
              max={0.99}
              step={0.01}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              onBlur={() =>
                threshold !== settings.curator_merge_threshold &&
                onUpdate({ curator_merge_threshold: threshold })
              }
              className="max-w-32"
            />
            <span className="text-xs text-muted-foreground">
              Lower = more candidates surfaced.
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function EmptyState({ onRun }: { onRun: () => void }) {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card p-10 text-center">
      <Sparkles className="mx-auto size-8 text-muted-foreground" />
      <p className="mt-3 text-sm font-bold">No reports yet.</p>
      <p className="mt-1 text-xs text-muted-foreground">
        The first weekly scan runs automatically on Monday. You can also run one now.
      </p>
      <Button onClick={onRun} variant="outline" size="sm" className="mt-4 rounded-full">
        Run scan now
      </Button>
    </div>
  );
}

function ReportView({ report }: { report: Report }) {
  const counts = report.payload.counts ?? {};
  const startedAt = useMemo(
    () => new Date(report.started_at).toLocaleString(),
    [report.started_at],
  );

  if (report.status === "pending") {
    return (
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-card p-4 text-sm">
        <Loader2 className="size-4 animate-spin text-muted-foreground" />
        <div>
          <p className="font-medium">Scan in progress…</p>
          <p className="text-xs text-muted-foreground">Started {startedAt}.</p>
        </div>
      </div>
    );
  }

  if (report.status === "failed") {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive-soft p-4 text-sm text-destructive-ink">
        <AlertTriangle className="size-4 shrink-0" />
        <div>
          <p className="font-bold">Scan failed</p>
          <p className="text-xs text-muted-foreground">
            {report.error_message || "Unknown error. Try running the scan again."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label="Outdated"
          value={counts.outdated ?? 0}
          icon={<Clock className="size-4 text-amber" />}
        />
        <StatCard
          label="Broken links"
          value={counts.broken_links ?? 0}
          icon={<Link2Off className="size-4 text-destructive" />}
        />
        <StatCard
          label="Merge candidates"
          value={counts.merge_clusters ?? 0}
          icon={<GitMerge className="size-4 text-brand" />}
        />
        <StatCard
          label="Knowledge gaps"
          value={counts.gaps ?? 0}
          icon={<FileWarning className="size-4 text-violet" />}
        />
        <StatCard
          label="Stub drafts"
          value={counts.stubs ?? 0}
          icon={<Sparkles className="size-4 text-success" />}
        />
      </section>

      <p className="text-xs text-muted-foreground">
        Last scan completed {startedAt}.
      </p>

      <OutdatedSection items={report.payload.outdated_documents ?? []} />
      <BrokenLinkSection items={report.payload.broken_links ?? []} />
      <MergeSection items={report.payload.merge_clusters ?? []} />
      <GapsSection
        items={report.payload.knowledge_gaps ?? []}
        stubs={report.payload.drafted_stubs ?? []}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="flex items-center gap-2 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
        {icon}
        <span>{label}</span>
      </div>
      <p className="mt-2.5 text-[28px] font-extrabold leading-none tracking-tight tabular-nums">
        {value}
      </p>
    </div>
  );
}

function OutdatedSection({ items }: { items: OutdatedDoc[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h2 className="text-[15px] font-bold">Outdated documents</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Last updated more than your configured cutoff ago. Review and refresh, or
        archive if no longer relevant.
      </p>
      <ul className="mt-3 space-y-2">
        {items.slice(0, 20).map((d) => (
          <li
            key={d.id}
            className="flex items-center justify-between gap-3 rounded-xl border border-border bg-card p-3 text-sm"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{d.name || "(untitled)"}</p>
              <p className="text-xs text-muted-foreground">
                {d.age_days != null ? `${d.age_days} days old` : "age unknown"}
                {d.source ? ` · ${d.source}` : ""}
              </p>
            </div>
            <a
              href={`/documents?focus=${encodeURIComponent(d.id)}`}
              className="text-xs font-medium text-brand underline-offset-2 hover:underline"
            >
              Open
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function BrokenLinkSection({ items }: { items: BrokenLink[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h2 className="text-[15px] font-bold">Broken links</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        URLs in document summaries that failed to resolve.
      </p>
      <ul className="mt-3 space-y-2">
        {items.slice(0, 30).map((l, idx) => (
          <li
            key={`${l.document_id}-${idx}`}
            className="rounded-xl border border-border bg-card p-3 text-sm"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium">
                  {l.document_name || "(untitled doc)"}
                </p>
                <a
                  href={l.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-0.5 inline-flex items-center gap-1 truncate text-xs text-muted-foreground hover:underline"
                >
                  {l.url}
                  <ExternalLink className="size-3 shrink-0" />
                </a>
              </div>
              <Badge className="text-xs">
                {l.status_code ? `HTTP ${l.status_code}` : l.reason}
              </Badge>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function MergeSection({ items }: { items: MergeCluster[] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h2 className="text-[15px] font-bold">Merge candidates</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Documents the Curator identified as likely duplicates. Decide which to keep,
        merge, or archive.
      </p>
      <ul className="mt-3 space-y-2">
        {items.slice(0, 20).map((cluster, idx) => (
          <li key={idx} className="rounded-xl border border-border bg-card p-3 text-sm">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-muted-foreground">
                {cluster.size} documents · {Math.round(cluster.avg_similarity * 100)}%
                avg similarity
              </p>
              <Badge variant="outline" className="text-xs">
                cluster
              </Badge>
            </div>
            <ul className="mt-2 space-y-1">
              {cluster.members.map((m) => (
                <li key={m.id} className="flex items-center justify-between gap-2">
                  <span className="truncate">{m.name || "(untitled)"}</span>
                  <a
                    href={`/documents?focus=${encodeURIComponent(m.id)}`}
                    className="text-xs font-medium text-brand underline-offset-2 hover:underline"
                  >
                    Open
                  </a>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

function GapsSection({
  items,
  stubs,
}: {
  items: KnowledgeGap[];
  stubs: DraftedStub[];
}) {
  if (items.length === 0) return null;
  const stubByTopic = new Map(stubs.map((s) => [s.topic, s.draft_id] as const));
  return (
    <section>
      <h2 className="text-[15px] font-bold">Knowledge gaps</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Topics your team asked about but the knowledge base couldn&apos;t answer.
        Stubs for the top gaps have been drafted — review and approve them in the
        Knowledge Gaps queue.
      </p>
      <ul className="mt-3 space-y-2">
        {items.slice(0, 20).map((g, idx) => {
          const draftId = stubByTopic.get(g.topic);
          return (
            <li key={idx} className="rounded-xl border border-border bg-card p-3 text-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{g.topic}</p>
                  {g.sample_queries.length > 0 && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      e.g. &ldquo;{g.sample_queries[0]}&rdquo;
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge className="text-xs">
                    {g.count} {g.count === 1 ? "query" : "queries"}
                  </Badge>
                  {draftId ? (
                    <a
                      href={`/admin/knowledge-gaps?draft=${encodeURIComponent(draftId)}`}
                      className="text-xs font-medium text-brand underline-offset-2 hover:underline"
                    >
                      Review stub
                    </a>
                  ) : g.has_pending_draft ? (
                    <a
                      href={`/admin/knowledge-gaps`}
                      className="text-xs text-muted-foreground underline-offset-2 hover:underline"
                    >
                      Draft pending
                    </a>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
