"use client";

import { use, useMemo } from "react";
import useSWR from "swr";
import { AlertTriangle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { BuilderShell } from "@/components/autoflows/builder-shell";
import type { ActionStep, AutoflowDraft, AutoflowRow } from "@/lib/autoflow/types";

const fetcher = async (url: string): Promise<AutoflowRow> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (res.status === 404) throw new Error("Autoflow not found.");
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

let _id = 0;
const nid = () => `step_edit_${++_id}`;

export default function EditAutoflowPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, error, isLoading } = useSWR<AutoflowRow>(
    `/api/admin/autoflows/${id}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const initial = useMemo<AutoflowDraft | null>(() => {
    if (!data) return null;
    const actions: ActionStep[] = (data.actions ?? [])
      .slice()
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      .map((a, i) => ({
        id: nid(),
        type: a.type,
        config: a.config ?? {},
        order: i,
      }));
    return {
      name: data.name,
      description: data.description,
      trigger_type: data.trigger_type,
      trigger_config: data.trigger_config ?? {},
      actions,
      confidence_threshold: data.confidence_threshold,
      is_active: data.is_active,
    };
  }, [data]);

  if (error) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <div className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive-soft p-4 text-sm text-destructive-ink">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      </div>
    );
  }

  if (isLoading || !initial) {
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-6">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return <BuilderShell initial={initial} mode="edit" autoflowId={id} />;
}
