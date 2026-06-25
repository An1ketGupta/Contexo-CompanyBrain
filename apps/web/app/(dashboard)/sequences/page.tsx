"use client";

import Link from "next/link";
import useSWR from "swr";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

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

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-zinc-100 text-zinc-700",
  scheduled: "bg-blue-100 text-blue-700",
  active: "bg-emerald-100 text-emerald-700",
  completed: "bg-zinc-100 text-zinc-500",
  cancelled: "bg-zinc-100 text-zinc-500",
};

export default function SequencesPage() {
  const { data, error, isLoading } = useSWR<ListResponse>(
    "/api/sequences",
    fetcher,
    { revalidateOnFocus: false },
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Follow-up sequences</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            AI-drafted 3-step email sequences for individual prospects. Sends from your
            Gmail; thread stays coherent across steps.
          </p>
        </div>
        <Button asChild>
          <Link href="/sequences/new" className="gap-2">
            <Plus className="size-4" />
            New sequence
          </Link>
        </Button>
      </header>

      {error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          {(error as Error).message}
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : !data?.sequences.length ? (
        <div className="rounded-md border border-dashed p-10 text-center text-sm text-muted-foreground">
          No sequences yet. Click <strong>New sequence</strong> to draft one.
        </div>
      ) : (
        <ul className="space-y-2">
          {data.sequences.map((s) => (
            <li key={s.id}>
              <Link
                href={`/sequences/${s.id}`}
                className="flex items-center justify-between gap-3 rounded-md border bg-card p-3 hover:bg-accent"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{s.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {s.prospect_name ? `${s.prospect_name} · ` : ""}
                    {s.prospect_email}
                  </p>
                </div>
                <Badge className={STATUS_COLORS[s.status] ?? "bg-zinc-100 text-zinc-700"}>
                  {s.status}
                </Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
