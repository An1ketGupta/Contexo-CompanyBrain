"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import {
  Activity,
  AlertTriangle,
  Copy,
  MoreVertical,
  Plus,
  Sparkles,
  Workflow,
  Zap,
} from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TemplatesGallery } from "@/components/autoflows/templates-gallery";
import { TRIGGER_CATALOG } from "@/lib/autoflow/triggers";
import { getAction } from "@/lib/autoflow/catalog";
import { getIcon } from "@/lib/autoflow/icons";
import type { AutoflowRow } from "@/lib/autoflow/types";

interface ListResponse {
  autoflows: AutoflowRow[];
}

const TRIGGER_LABEL = Object.fromEntries(
  TRIGGER_CATALOG.map((t) => [t.type, t.label]),
);

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function AutoflowsPage() {
  const router = useRouter();
  const [galleryOpen, setGalleryOpen] = useState(false);
  const { data, error, isLoading, mutate } = useSWR<ListResponse>(
    "/api/admin/autoflows",
    fetcher,
    { revalidateOnFocus: false },
  );

  const togglePromise = async (flow: AutoflowRow) => {
    const next = !flow.is_active;
    await mutate(
      data
        ? {
            ...data,
            autoflows: data.autoflows.map((f) =>
              f.id === flow.id ? { ...f, is_active: next } : f,
            ),
          }
        : data,
      { revalidate: false },
    );
    try {
      const res = await fetch(`/api/admin/autoflows/${flow.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: next }),
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      toast.success(next ? "Autoflow activated" : "Autoflow paused");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
      mutate();
    }
  };

  const duplicate = async (flow: AutoflowRow) => {
    try {
      const res = await fetch(`/api/admin/autoflows/${flow.id}/duplicate`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      const created = await res.json();
      toast.success("Duplicated");
      router.push(`/admin/autoflows/${created.id}/edit`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Duplicate failed");
    }
  };

  const remove = async (flow: AutoflowRow) => {
    if (!confirm(`Delete "${flow.name}"? This can't be undone.`)) return;
    try {
      const res = await fetch(`/api/admin/autoflows/${flow.id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) throw new Error(`Failed (${res.status})`);
      toast.success("Deleted");
      mutate();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex flex-wrap items-start justify-between gap-5">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight">Autoflows</h1>
          <p className="mt-1 max-w-[64ch] text-[15px] leading-relaxed text-muted-foreground">
            Trigger → action automations. When the event fires, the steps run in order.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild variant="outline" className="gap-2">
            <Link href="/admin/autoflows/activity">
              <Activity className="size-4" /> Activity
            </Link>
          </Button>
          <Button
            variant="outline"
            className="gap-2"
            onClick={() => setGalleryOpen(true)}
          >
            <Sparkles className="size-4" /> Templates
          </Button>
          <Button asChild className="gap-2">
            <Link href="/admin/autoflows/new">
              <Plus className="size-4" /> New autoflow
            </Link>
          </Button>
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive-soft p-4 text-sm text-destructive-ink">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : !data?.autoflows.length ? (
        <EmptyState onUseTemplate={() => setGalleryOpen(true)} />
      ) : (
        <ul className="space-y-3">
          {data.autoflows.map((flow) => (
            <FlowCard
              key={flow.id}
              flow={flow}
              onToggle={() => togglePromise(flow)}
              onDuplicate={() => duplicate(flow)}
              onDelete={() => remove(flow)}
            />
          ))}
        </ul>
      )}

      <TemplatesGallery
        open={galleryOpen}
        onOpenChange={setGalleryOpen}
        onPick={(t) => router.push(`/admin/autoflows/new?template=${t.id}`)}
      />
    </div>
  );
}

function FlowCard({
  flow,
  onToggle,
  onDuplicate,
  onDelete,
}: {
  flow: AutoflowRow;
  onToggle: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}) {
  const triggerLabel = TRIGGER_LABEL[flow.trigger_type] ?? flow.trigger_type;
  const actions = flow.actions
    .slice()
    .sort((a, b) => a.order - b.order)
    .slice(0, 4);
  const overflow = flow.actions.length - actions.length;

  return (
    <li className="rounded-2xl border border-border bg-card p-5 transition-colors hover:bg-muted/40">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <Link
          href={`/admin/autoflows/${flow.id}`}
          className="min-w-0 flex-1 space-y-2"
        >
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-[15px] font-bold">{flow.name}</h2>
            {!flow.is_active && <Badge variant="outline">Paused</Badge>}
            {flow.confidence_threshold != null && (
              <Badge variant="brand">
                Gate ≥ {(flow.confidence_threshold * 100).toFixed(0)}%
              </Badge>
            )}
          </div>
          {flow.description && (
            <p className="line-clamp-2 text-xs text-muted-foreground">{flow.description}</p>
          )}
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <Badge variant="outline" className="gap-1">
              <Zap className="size-3" />
              {triggerLabel}
              {flow.trigger_type === "scheduled" && flow.trigger_config?.cron
                ? ` · ${flow.trigger_config.cron}`
                : ""}
            </Badge>
            <span className="text-muted-foreground">→</span>
            {actions.map((a, i) => {
              const entry = getAction(a.type);
              const Icon = getIcon(entry.icon);
              return (
                <Badge key={i} variant="outline" className="gap-1">
                  <Icon className="size-3" />
                  {entry.shortLabel}
                </Badge>
              );
            })}
            {overflow > 0 && (
              <Badge variant="outline">+{overflow} more</Badge>
            )}
          </div>
        </Link>
        <div className="flex items-center gap-2">
          <div className="text-right text-xs text-muted-foreground">
            {flow.last_fired_at ? (
              <span>last fired {new Date(flow.last_fired_at).toLocaleString()}</span>
            ) : (
              <span className="italic">never fired</span>
            )}
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" aria-label="Actions">
                <MoreVertical className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onToggle}>
                {flow.is_active ? "Pause" : "Activate"}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onDuplicate}>
                <Copy className="mr-2 size-3.5" /> Duplicate
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onDelete} className="text-destructive focus:text-destructive">
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </li>
  );
}

function EmptyState({ onUseTemplate }: { onUseTemplate: () => void }) {
  return (
    <div className="space-y-4 rounded-2xl border border-dashed border-border bg-muted/40 px-6 py-12 text-center">
      <Workflow className="mx-auto size-8 text-muted-foreground" />
      <div>
        <p className="text-sm font-semibold">No autoflows yet</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Autoflows fire on events — document uploads, knowledge gaps, schedules, and more.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button onClick={onUseTemplate} className="gap-2">
          <Sparkles className="size-4" /> Start from a template
        </Button>
        <Button asChild variant="outline" className="gap-2">
          <Link href="/admin/autoflows/new">
            <Plus className="size-4" /> Blank flow
          </Link>
        </Button>
      </div>
    </div>
  );
}
