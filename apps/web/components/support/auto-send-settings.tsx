"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Send, ShieldCheck } from "lucide-react";
import useSWR from "swr";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

interface AutoSendConfig {
  enabled: boolean;
  threshold: number;
  sender_user_id: string | null;
}

const SETTINGS_URL = "/api/admin/support/settings/auto-send";

const fetcher = async (url: string): Promise<AutoSendConfig> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load auto-send settings (${res.status})`);
  return res.json();
};

export function AutoSendSettings() {
  const { data, error, isLoading, mutate } = useSWR<AutoSendConfig>(SETTINGS_URL, fetcher);

  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState("8.0");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Sync local edit state whenever the server value (re)loads.
  useEffect(() => {
    if (data) {
      setEnabled(data.enabled);
      setThreshold(data.threshold.toFixed(1));
    }
  }, [data]);

  async function save(next: Partial<AutoSendConfig>) {
    setSaving(true);
    setSaveError(null);
    const payload: AutoSendConfig = {
      enabled: next.enabled ?? enabled,
      threshold: Number(next.threshold ?? Number(threshold)),
      sender_user_id: data?.sender_user_id ?? null,
    };
    if (Number.isNaN(payload.threshold) || payload.threshold < 0 || payload.threshold > 10) {
      setSaveError("Threshold must be a number between 0 and 10.");
      setSaving(false);
      return;
    }
    try {
      const res = await fetch(SETTINGS_URL, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`Save failed (${res.status})`);
      const saved: AutoSendConfig = await res.json();
      await mutate(saved, { revalidate: false });
      setSavedAt(Date.now());
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mb-6 rounded-2xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-bold">
            <Send className="h-4 w-4 text-brand" />
            Auto-send
          </h2>
          <p className="mt-1 max-w-xl text-xs text-muted-foreground">
            When on, drafts scoring at or above the threshold are sent automatically
            via Gmail — everything else stays here for review. Escalation drafts
            (&ldquo;I don&rsquo;t have a confident answer&rdquo;) are never auto-sent.
          </p>
        </div>
        <Button
          size="sm"
          variant={enabled ? "primary" : "outline"}
          disabled={saving || isLoading}
          onClick={() => {
            const next = !enabled;
            setEnabled(next);
            void save({ enabled: next });
          }}
          className="rounded-full"
        >
          {saving ? (
            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
          ) : null}
          {enabled ? "On" : "Off"}
        </Button>
      </div>

      {enabled && (
        <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-border pt-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="autosend-threshold" className="text-xs">
              Confidence threshold (0&ndash;10)
            </Label>
            <Input
              id="autosend-threshold"
              type="number"
              min={0}
              max={10}
              step={0.5}
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              className="h-9 w-28 font-mono text-sm"
            />
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={saving}
            onClick={() => void save({})}
          >
            Save threshold
          </Button>
          <p className="flex items-center gap-1 text-xs text-muted-foreground">
            <ShieldCheck className="h-3.5 w-3.5 text-success-ink" />
            Requires an admin with Gmail connected (send scope).
          </p>
        </div>
      )}

      {(error || saveError) && (
        <div className="mt-3 flex items-center gap-2 text-xs text-destructive-ink">
          <AlertTriangle className="h-3.5 w-3.5" />
          {String(saveError || (error as Error)?.message || error)}
        </div>
      )}
      {savedAt && !saveError && (
        <div className={cn("mt-3 text-xs text-success-ink")}>Saved.</div>
      )}
    </div>
  );
}
