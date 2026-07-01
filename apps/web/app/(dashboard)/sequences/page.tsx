"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PageHeader,
  Stat,
  StatGrid,
  StatusPill,
  type PillTone,
} from "@/components/actual/kit";
import { cn } from "@/lib/utils";

interface SequenceRow {
  id: string;
  name: string;
  prospect_email: string;
  prospect_name: string | null;
  status: string;
  created_at: string;
}

interface ListResponse {
  sequences: SequenceRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_TONE: Record<string, PillTone> = {
  draft: "gray",
  scheduled: "blue",
  active: "green",
  completed: "gray",
  cancelled: "red",
};

const FILTERS = ["All", "Active", "Scheduled", "Draft"] as const;
type Filter = (typeof FILTERS)[number];

export default function SequencesPage() {
  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/sequences",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [filter, setFilter] = useState<Filter>("All");
  const sequences = useMemo(() => data?.sequences ?? [], [data]);

  const stats = useMemo(() => {
    const by = (s: string) => sequences.filter((r) => r.status === s).length;
    return {
      total: sequences.length,
      active: by("active"),
      scheduled: by("scheduled"),
      draft: by("draft"),
    };
  }, [sequences]);

  const rows = useMemo(
    () =>
      filter === "All"
        ? sequences
        : sequences.filter((r) => r.status === filter.toLowerCase()),
    [sequences, filter],
  );

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6 md:p-8">
      <PageHeader
        eyebrow="Outbound"
        title="Follow-up sequences"
        description="AI-drafted 3-step email sequences for individual prospects. Sends from your Gmail; the thread stays coherent across every step."
        actions={
          <Button asChild>
            <Link href="/sequences/new">
              <Plus className="size-4" />
              New sequence
            </Link>
          </Button>
        }
      />

      <StatGrid>
        <Stat label="Total sequences" value={stats.total} />
        <Stat label="Active" value={stats.active} tone="up" />
        <Stat label="Scheduled" value={stats.scheduled} />
        <Stat label="Drafts" value={stats.draft} />
      </StatGrid>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex gap-1 rounded-xl bg-muted p-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={cn(
                "rounded-lg px-3.5 py-1.5 text-[13px] font-bold transition-colors",
                filter === f
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {f}
            </button>
          ))}
        </div>
        <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
          {rows.length} shown
        </span>
      </div>

      {error ? (
        <div className="rounded-2xl border border-destructive/40 bg-destructive-soft p-4 text-sm text-destructive">
          {(error as Error).message}
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-2xl" />
          ))}
        </div>
      ) : !sequences.length ? (
        <EmptyState />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="grid grid-cols-[1fr_auto] gap-4 border-b border-border bg-muted/60 px-5 py-3 font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <span>Sequence</span>
            <span>Status</span>
          </div>
          {rows.map((s) => (
            <Link
              key={s.id}
              href={`/sequences/${s.id}`}
              className="grid grid-cols-[1fr_auto] items-center gap-4 border-b border-border px-5 py-4 transition-colors last:border-b-0 hover:bg-muted/40"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-bold text-foreground">
                  {s.name}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {s.prospect_name ? `${s.prospect_name} · ` : ""}
                  {s.prospect_email}
                </p>
              </div>
              <StatusPill tone={STATUS_TONE[s.status] ?? "gray"}>
                {s.status}
              </StatusPill>
            </Link>
          ))}
          {!rows.length ? (
            <p className="px-5 py-10 text-center text-sm text-muted-foreground">
              No {filter.toLowerCase()} sequences.
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-background p-12 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-brand-tint text-brand">
        <Plus className="h-5 w-5" />
      </div>
      <p className="mt-3 text-sm font-bold">No sequences yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Click <span className="font-semibold text-foreground">New sequence</span>{" "}
        to draft a grounded 3-step follow-up.
      </p>
    </div>
  );
}
