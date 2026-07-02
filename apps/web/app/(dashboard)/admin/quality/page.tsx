"use client";

import { useState } from "react";
import useSWR from "swr";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

interface TrendRow {
  week_start: string;
  avg_score: number;
  message_count: number;
}
interface CategoryRow {
  category: string;
  avg_score: number;
  message_count: number;
}
interface QualityResponse {
  trend: TrendRow[];
  by_category: CategoryRow[];
}

const fetcher = async (url: string): Promise<QualityResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

export default function AdminQualityPage() {
  const { data, error, isLoading, mutate } = useSWR<QualityResponse>(
    "/api/admin/quality-metrics?weeks=8",
    fetcher,
    { revalidateOnFocus: false },
  );

  const [thresholdInput, setThresholdInput] = useState("");
  const [savingThreshold, setSavingThreshold] = useState(false);
  const [backfilling, setBackfilling] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const handleSaveThreshold = async () => {
    setMsg(null);
    setSavingThreshold(true);
    try {
      const val = thresholdInput.trim() === "" ? null : Number(thresholdInput);
      await fetch("/api/admin/quality-metrics/threshold", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threshold: val }),
      });
      setMsg("Threshold saved.");
    } finally {
      setSavingThreshold(false);
    }
  };

  const handleBackfill = async () => {
    setMsg(null);
    setBackfilling(true);
    try {
      const res = await fetch("/api/admin/quality-metrics/backfill?days=30", {
        method: "POST",
      });
      const body = await res.json();
      setMsg(`Backfill: scanned ${body.messages_scanned}, scored ${body.scored}.`);
      await mutate();
    } finally {
      setBackfilling(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header>
        <h1 className="text-2xl font-extrabold tracking-tight">Output quality</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Weekly trend of message quality (0–10) computed from copy + feedback
          + confidence + sources.
        </p>
      </header>

      {isLoading ? (
        <Skeleton className="h-40 w-full rounded-2xl" />
      ) : error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive-soft p-3 text-sm text-destructive-ink">
          Failed to load metrics. (Are you signed in as an admin?)
        </div>
      ) : (
        <>
          <section className="rounded-2xl border border-border bg-card p-6">
            <h2 className="mb-3 text-[15px] font-bold">Weekly trend</h2>
            {!data?.trend?.length ? (
              <p className="text-sm text-muted-foreground">
                No data yet. Run a backfill to populate from the last 30 days.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                    <th className="pb-2">Week</th>
                    <th className="pb-2">Average score</th>
                    <th className="pb-2">Messages</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trend.map((r, i) => (
                    <tr key={i} className="border-b last:border-b-0">
                      <td className="py-2">
                        {new Date(r.week_start).toLocaleDateString()}
                      </td>
                      <td className="py-2 font-mono">
                        {r.avg_score?.toFixed(2)}
                      </td>
                      <td className="py-2 text-muted-foreground">
                        {r.message_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="rounded-2xl border border-border bg-card p-6">
            <h2 className="mb-3 text-[15px] font-bold">By category</h2>
            {!data?.by_category?.length ? (
              <p className="text-sm text-muted-foreground">
                No category breakdown available.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                    <th className="pb-2">Category</th>
                    <th className="pb-2">Average score</th>
                    <th className="pb-2">Messages</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_category.map((c, i) => (
                    <tr key={i} className="border-b last:border-b-0">
                      <td className="py-2">{c.category}</td>
                      <td className="py-2 font-mono">{c.avg_score?.toFixed(2)}</td>
                      <td className="py-2 text-muted-foreground">{c.message_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}

      <section className="rounded-2xl border border-border bg-card p-6">
        <h2 className="mb-3 text-[15px] font-bold">Settings</h2>
        <div className="flex items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="threshold">Alert when weekly avg drops below</Label>
            <Input
              id="threshold"
              type="number"
              min="0"
              max="10"
              step="0.1"
              placeholder="e.g. 6.5"
              value={thresholdInput}
              onChange={(e) => setThresholdInput(e.target.value)}
              className="w-40"
            />
          </div>
          <Button
            onClick={handleSaveThreshold}
            disabled={savingThreshold}
            className="rounded-full"
          >
            {savingThreshold ? "Saving…" : "Save threshold"}
          </Button>
          <Button
            variant="outline"
            onClick={handleBackfill}
            disabled={backfilling}
            className="rounded-full"
          >
            {backfilling ? "Backfilling…" : "Backfill last 30 days"}
          </Button>
        </div>
        {msg && <p className="mt-3 text-sm font-medium text-success-ink">{msg}</p>}
      </section>
    </div>
  );
}
