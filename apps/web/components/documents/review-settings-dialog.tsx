"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { mutate } from "swr";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { formatAbsolute, formatRelativeShort } from "@/lib/date";
import { cn } from "@/lib/utils";

interface ReviewSettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string;
  documentName: string;
  currentFrequencyDays: number | null;
  lastReviewedAt: string | null;
  reviewDueAt: string | null;
}

const PRESETS = [
  { value: "off", label: "Off" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "180 days" },
  { value: "365", label: "365 days" },
  { value: "custom", label: "Custom…" },
] as const;

type PresetValue = (typeof PRESETS)[number]["value"];

const MIN_DAYS = 7;
const MAX_DAYS = 730;

function presetFor(days: number | null | undefined): PresetValue {
  if (days == null) return "off";
  if (days === 30 || days === 90 || days === 180 || days === 365) {
    return String(days) as PresetValue;
  }
  return "custom";
}

export function ReviewSettingsDialog({
  open,
  onOpenChange,
  documentId,
  documentName,
  currentFrequencyDays,
  lastReviewedAt,
  reviewDueAt,
}: ReviewSettingsDialogProps) {
  const [preset, setPreset] = useState<PresetValue>(presetFor(currentFrequencyDays));
  const [customDays, setCustomDays] = useState<string>(
    currentFrequencyDays != null && presetFor(currentFrequencyDays) === "custom"
      ? String(currentFrequencyDays)
      : "",
  );
  const [saving, setSaving] = useState(false);
  const [marking, setMarking] = useState(false);

  useEffect(() => {
    if (open) {
      setPreset(presetFor(currentFrequencyDays));
      setCustomDays(
        currentFrequencyDays != null && presetFor(currentFrequencyDays) === "custom"
          ? String(currentFrequencyDays)
          : "",
      );
    }
  }, [open, currentFrequencyDays]);

  const customValid = (() => {
    if (preset !== "custom") return true;
    const n = Number(customDays);
    return Number.isInteger(n) && n >= MIN_DAYS && n <= MAX_DAYS;
  })();

  const resolveDays = (): number | null => {
    if (preset === "off") return null;
    if (preset === "custom") {
      const n = Number(customDays);
      return Number.isInteger(n) && n >= MIN_DAYS && n <= MAX_DAYS ? n : null;
    }
    return Number(preset);
  };

  const dirty = (() => {
    const next = resolveDays();
    return next !== (currentFrequencyDays ?? null);
  })();

  const save = async () => {
    if (!customValid) return;
    const days = resolveDays();
    setSaving(true);
    try {
      const res = await fetch(`/api/documents/${documentId}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_frequency_days: days }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      toast.success(
        days == null
          ? `Review reminders off for "${documentName}".`
          : `Will remind every ${days} days.`,
      );
      await mutate(
        (key) => typeof key === "string" && key.startsWith("/api/documents"),
      );
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  const markReviewed = async () => {
    setMarking(true);
    try {
      const res = await fetch(
        `/api/documents/${documentId}/mark-reviewed`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Failed (${res.status})`);
      }
      toast.success(`Marked "${documentName}" as reviewed.`);
      await mutate(
        (key) => typeof key === "string" && key.startsWith("/api/documents"),
      );
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not mark reviewed.");
    } finally {
      setMarking(false);
    }
  };

  const busy = saving || marking;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Review schedule</DialogTitle>
          <DialogDescription className="line-clamp-2">
            How often should an admin be nudged to revisit{" "}
            <span className="font-medium text-foreground">{documentName}</span>?
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <label
              htmlFor="review-cadence"
              className="text-xs font-medium text-foreground"
            >
              Review cadence
            </label>
            <select
              id="review-cadence"
              value={preset}
              onChange={(e) => setPreset(e.target.value as PresetValue)}
              disabled={busy}
              className={cn(
                "flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm",
                "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              {PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
            {preset === "custom" && (
              <div className="flex items-center gap-2 pt-1">
                <span className="text-xs text-muted-foreground">
                  Remind every
                </span>
                <Input
                  type="number"
                  min={MIN_DAYS}
                  max={MAX_DAYS}
                  step={1}
                  value={customDays}
                  onChange={(e) => setCustomDays(e.target.value)}
                  disabled={busy}
                  className="h-8 w-20"
                  aria-label="Custom review cadence in days"
                />
                <span className="text-xs text-muted-foreground">
                  days ({MIN_DAYS}–{MAX_DAYS})
                </span>
              </div>
            )}
            {preset === "custom" && !customValid && customDays !== "" && (
              <p className="text-[11px] text-destructive">
                Must be a whole number between {MIN_DAYS} and {MAX_DAYS}.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3 rounded-md border border-border/70 bg-muted/30 px-3 py-2.5 text-xs">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Last reviewed
              </p>
              <p
                className="mt-0.5 font-medium text-foreground"
                title={lastReviewedAt ? formatAbsolute(lastReviewedAt) : undefined}
              >
                {lastReviewedAt ? formatRelativeShort(lastReviewedAt) : "Never"}
              </p>
            </div>
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Next due
              </p>
              <p
                className="mt-0.5 font-medium text-foreground"
                title={reviewDueAt ? formatAbsolute(reviewDueAt) : undefined}
              >
                {reviewDueAt ? formatRelativeShort(reviewDueAt) : "—"}
              </p>
            </div>
          </div>

          {lastReviewedAt != null || currentFrequencyDays != null ? (
            <Button
              variant="outline"
              size="sm"
              onClick={markReviewed}
              disabled={busy}
              className="w-full"
            >
              {marking ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5" />
              )}  
              Mark as reviewed now
            </Button>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button onClick={save} disabled={busy || !dirty || !customValid}>
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : null}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
