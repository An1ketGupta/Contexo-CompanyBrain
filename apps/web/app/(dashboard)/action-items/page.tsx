"use client";

import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface ActionItem {
  id: string;
  action_text: string;
  owner_name_raw: string | null;
  owner_user_id: string | null;
  due_date: string | null;
  status: string;
  external_provider: string | null;
  external_url: string | null;
}

const fetcher = async (url: string): Promise<{ action_items: ActionItem[] }> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

const STATUS_BADGE: Record<string, string> = {
  pending: "bg-secondary text-muted-foreground",
  tracked: "bg-brand-tint text-brand",
  completed: "bg-success-tint text-success",
  overdue: "bg-destructive-soft text-destructive",
  cancelled: "bg-secondary text-muted-foreground",
};

export default function ActionItemsPage() {
  const { data, isLoading } = useSWR<{ action_items: ActionItem[] }>(
    "/api/action-items",
    fetcher,
    { revalidateOnFocus: false },
  );
  const { mutate } = useSWRConfig();

  const [notes, setNotes] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractedIds, setExtractedIds] = useState<string[]>([]);
  const [target, setTarget] = useState<"notion" | "asana" | "linear">("asana");
  const [notionParent, setNotionParent] = useState("");
  const [provisioning, setProvisioning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExtract = async () => {
    setError(null);
    setExtracting(true);
    try {
      const res = await fetch("/api/action-items/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Failed (${res.status})`);
      }
      const body = await res.json();
      setExtractedIds((body.action_items ?? []).map((i: ActionItem) => i.id));
      setNotes("");
      await mutate("/api/action-items");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Extract failed");
    } finally {
      setExtracting(false);
    }
  };

  const handleProvision = async () => {
    if (!extractedIds.length) return;
    setError(null);
    setProvisioning(true);
    try {
      const res = await fetch("/api/action-items/create-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_item_ids: extractedIds,
          target,
          notion_parent_page_id: target === "notion" ? notionParent : null,
        }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(b?.detail || `Failed (${res.status})`);
      }
      setExtractedIds([]);
      await mutate("/api/action-items");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Provision failed");
    } finally {
      setProvisioning(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-extrabold tracking-tight">Action items</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste meeting notes — we extract owners + due dates and push tasks to
          Notion, Asana, or Linear.
        </p>
      </header>

      <section className="space-y-4 rounded-xl border border-border bg-card p-6">
        <div className="space-y-2">
          <Label htmlFor="notes">Paste meeting notes</Label>
          <Textarea
            id="notes"
            rows={8}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Bullet points, transcript snippets, anything readable. We&apos;ll pick out commitments."
          />
        </div>

        {error && (
          <div className="rounded-lg border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="flex justify-end">
          <Button onClick={handleExtract} disabled={extracting || notes.trim().length < 10}>
            {extracting ? "Extracting…" : "Extract action items"}
          </Button>
        </div>

        {extractedIds.length > 0 && (
          <div className="rounded-lg border border-amber/30 bg-amber-tint p-4">
            <p className="text-sm">
              {extractedIds.length} item{extractedIds.length === 1 ? "" : "s"}
              {" "}extracted. Provision them?
            </p>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label htmlFor="target" className="text-xs">
                  Target
                </Label>
                <select
                  id="target"
                  value={target}
                  onChange={(e) => setTarget(e.target.value as typeof target)}
                  className="h-9 w-40 rounded-[10px] border border-input bg-background px-2.5 text-sm transition-colors focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
                >
                  <option value="asana">Asana</option>
                  <option value="linear">Linear</option>
                  <option value="notion">Notion</option>
                </select>
              </div>
              {target === "notion" && (
                <div className="space-y-1">
                  <Label htmlFor="np" className="text-xs">
                    Notion parent page id
                  </Label>
                  <input
                    id="np"
                    value={notionParent}
                    onChange={(e) => setNotionParent(e.target.value)}
                    className="h-9 w-72 rounded-[10px] border border-input bg-background px-2.5 text-sm transition-colors focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
                  />
                </div>
              )}
              <Button onClick={handleProvision} disabled={provisioning}>
                {provisioning ? "Provisioning…" : "Create tasks"}
              </Button>
            </div>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          Recent action items
        </h2>
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : !data?.action_items?.length ? (
          <p className="text-sm text-muted-foreground">No action items yet.</p>
        ) : (
          <ul className="space-y-2">
            {data.action_items.map((a) => (
              <li key={a.id} className="rounded-xl border border-border bg-card p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="text-sm">{a.action_text}</div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {a.owner_name_raw && <>Owner: {a.owner_name_raw}</>}
                      {a.due_date && <> · Due {a.due_date}</>}
                      {a.external_provider && a.external_url && (
                        <>
                          {" "}·{" "}
                          <a
                            href={a.external_url}
                            target="_blank"
                            rel="noreferrer"
                            className="font-medium text-brand hover:underline"
                          >
                            {a.external_provider} ↗
                          </a>
                        </>
                      )}
                    </div>
                  </div>
                  <Badge className={STATUS_BADGE[a.status] ?? ""}>{a.status}</Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
