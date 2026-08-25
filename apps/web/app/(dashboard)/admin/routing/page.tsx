"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, Check, FolderInput, X } from "lucide-react";
import { toast } from "sonner";

import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";

interface SuggestionRow {
  id: string;
  document_id: string;
  collection_id: string;
  suggested_tag: string;
  similarity: number;
  status: string;
  created_at: string;
  document_name: string | null;
  collection_name: string | null;
  collection_color: string | null;
  collection_icon: string | null;
}

interface ListResponse {
  suggestions: SuggestionRow[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (res.status === 403) throw new Error("Admin access required.");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

export default function RoutingPage() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [working, setWorking] = useState(false);

  const { data, error, isLoading, mutate } = useSWR<ListResponse>(
    "/api/admin/routing",
    fetcher,
    { revalidateOnFocus: false },
  );

  const grouped = useMemo(() => {
    const out = new Map<string, SuggestionRow[]>();
    for (const s of data?.suggestions ?? []) {
      const key = s.collection_id;
      const arr = out.get(key) ?? [];
      arr.push(s);
      out.set(key, arr);
    }
    return out;
  }, [data]);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllInGroup = (rows: SuggestionRow[]) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const r of rows) next.add(r.id);
      return next;
    });
  };

  const act = async (path: "accept" | "reject") => {
    if (selected.size === 0) return;
    setWorking(true);
    try {
      const res = await fetch(`/api/admin/routing/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ suggestion_ids: Array.from(selected) }),
      });
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      setSelected(new Set());
      mutate();
      toast.success(
        path === "accept"
          ? "Suggestions accepted — tags added to documents."
          : "Suggestions rejected.",
      );
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Smart routing</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Documents that fit existing collections based on their summary embedding.
            Accepting a suggestion adds the collection&apos;s tag to the document so the
            existing tag-based filter pulls it in automatically.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => act("reject")}
            disabled={working || selected.size === 0}
            className="gap-2"
          >
            <X className="size-4" /> Reject ({selected.size})
          </Button>
          <Button
            size="sm"
            onClick={() => act("accept")}
            disabled={working || selected.size === 0}
            className="gap-2"
          >
            <Check className="size-4" /> Accept ({selected.size})
          </Button>
        </div>
      </header>

      {error ? (
        <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          <span>{(error as Error).message}</span>
        </div>
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : !data?.suggestions.length ? (
        <div className="rounded-md border border-dashed p-10 text-center">
          <FolderInput className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No routing suggestions yet.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Smart routing fires after a document finishes ingesting if it matches an
            existing collection. Build collections first and seed them with a few docs
            so centroids have data to work with.
          </p>
        </div>
      ) : (
        <ul className="space-y-4">
          {Array.from(grouped.entries()).map(([collectionId, rows]) => {
            const first = rows[0];
            return (
              <li key={collectionId} className="rounded-md border bg-card p-4">
                <header className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    {first.collection_icon && (
                      <span aria-hidden>{first.collection_icon}</span>
                    )}
                    <h2 className="text-sm font-medium">
                      {first.collection_name ?? "(deleted collection)"}
                    </h2>
                    <Badge variant="outline">{rows.length} suggestion{rows.length === 1 ? "" : "s"}</Badge>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => selectAllInGroup(rows)}
                    className="text-xs"
                  >
                    Select all
                  </Button>
                </header>
                <ul className="mt-3 space-y-2">
                  {rows.map((r) => (
                    <li
                      key={r.id}
                      className="flex items-center gap-3 rounded border px-3 py-2 text-sm"
                    >
                      <Checkbox
                        checked={selected.has(r.id)}
                        onCheckedChange={() => toggle(r.id)}
                        aria-label={`Select ${r.document_name}`}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium">
                          {r.document_name ?? "(unknown doc)"}
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          Would add tag{" "}
                          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                            {r.suggested_tag}
                          </code>
                        </p>
                      </div>
                      <Badge variant="default" className="text-xs">
                        {(r.similarity * 100).toFixed(0)}% match
                      </Badge>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
