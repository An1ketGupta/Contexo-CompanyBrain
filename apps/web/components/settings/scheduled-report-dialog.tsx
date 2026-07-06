"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  useScheduledReports,
  type ReportFrequency,
  type ReportType,
  type ScheduledReport,
} from "@/hooks/use-scheduled-reports";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  report?: ScheduledReport | null;
  defaultRecipient?: string;
}

const DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const REPORT_TYPES: { value: ReportType; label: string; hint: string }[] = [
  {
    value: "usage_summary",
    label: "Usage summary",
    hint: "Queries, active users, time saved, feedback signals",
  },
  {
    value: "knowledge_health",
    label: "Knowledge health",
    hint: "Top docs, stale docs, knowledge gaps",
  },
];

const FREQUENCIES: ReportFrequency[] = ["daily", "weekly", "monthly"];

export function ScheduledReportDialog({
  open,
  onOpenChange,
  report,
  defaultRecipient,
}: Props) {
  const { createReport, updateReport } = useScheduledReports();
  const isEdit = !!report;

  const [reportType, setReportType] = useState<ReportType>("usage_summary");
  const [frequency, setFrequency] = useState<ReportFrequency>("weekly");
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [sendTimeUtc, setSendTimeUtc] = useState(8);
  const [recipients, setRecipients] = useState<string[]>([]);
  const [recipientInput, setRecipientInput] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setReportType(report?.report_type ?? "usage_summary");
    setFrequency(report?.frequency ?? "weekly");
    setDayOfWeek(report?.day_of_week ?? 0);
    setDayOfMonth(report?.day_of_month ?? 1);
    setSendTimeUtc(report?.send_time_utc ?? 8);
    setRecipients(
      report?.recipients ?? (defaultRecipient ? [defaultRecipient] : []),
    );
    setRecipientInput("");
  }, [open, report, defaultRecipient]);

  function commitRecipient() {
    const value = recipientInput.trim();
    if (!value) return;
    // Loose email validation — server re-validates with EmailStr.
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) {
      toast.error("Enter a valid email address");
      return;
    }
    if (recipients.some((r) => r.toLowerCase() === value.toLowerCase())) {
      setRecipientInput("");
      return;
    }
    if (recipients.length >= 20) {
      toast.error("Maximum 20 recipients per report");
      return;
    }
    setRecipients([...recipients, value]);
    setRecipientInput("");
  }

  function removeRecipient(addr: string) {
    setRecipients(recipients.filter((r) => r !== addr));
  }

  async function save() {
    if (recipients.length === 0) {
      toast.error("Add at least one recipient");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        report_type: reportType,
        frequency,
        recipients,
        send_time_utc: sendTimeUtc,
        day_of_week: frequency === "weekly" ? dayOfWeek : null,
        day_of_month: frequency === "monthly" ? dayOfMonth : null,
      };
      if (isEdit && report) {
        await updateReport(report.id, payload);
        toast.success("Report updated");
      } else {
        await createReport(payload);
        toast.success("Report scheduled");
      }
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save report");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit scheduled report" : "New scheduled report"}</DialogTitle>
          <DialogDescription>
            Recurring email delivered to your inbox at the chosen UTC time.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Report type */}
          <div className="space-y-2">
            <Label>Report type</Label>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {REPORT_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => setReportType(t.value)}
                  className={cn(
                    "rounded-lg border p-3 text-left transition-colors",
                    reportType === t.value
                      ? "border-brand bg-brand-tint"
                      : "border-border hover:border-foreground/30",
                  )}
                >
                  <p className="text-sm font-medium">{t.label}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{t.hint}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Frequency */}
          <div className="space-y-2">
            <Label>Frequency</Label>
            <div className="flex gap-2">
              {FREQUENCIES.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFrequency(f)}
                  className={cn(
                    "flex-1 rounded-md border px-3 py-2 text-sm capitalize transition-colors",
                    frequency === f
                      ? "border-brand bg-brand-tint"
                      : "border-border hover:border-foreground/30",
                  )}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          {/* Day pickers */}
          {frequency === "weekly" && (
            <div className="space-y-2">
              <Label>Day of week</Label>
              <div className="flex gap-1">
                {DAYS_OF_WEEK.map((label, i) => (
                  <button
                    key={label}
                    type="button"
                    onClick={() => setDayOfWeek(i)}
                    className={cn(
                      "flex-1 rounded-md border py-1.5 text-xs font-medium transition-colors",
                      dayOfWeek === i
                        ? "border-brand bg-brand-tint"
                        : "border-border hover:border-foreground/30",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {frequency === "monthly" && (
            <div className="space-y-2">
              <Label htmlFor="day-of-month">Day of month (1–28)</Label>
              <Input
                id="day-of-month"
                type="number"
                min={1}
                max={28}
                value={dayOfMonth}
                onChange={(e) =>
                  setDayOfMonth(
                    Math.max(1, Math.min(28, parseInt(e.target.value, 10) || 1)),
                  )
                }
              />
            </div>
          )}

          {/* Time */}
          <div className="space-y-2">
            <Label htmlFor="send-time">Send time (UTC)</Label>
            <select
              id="send-time"
              value={sendTimeUtc}
              onChange={(e) => setSendTimeUtc(parseInt(e.target.value, 10))}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              {Array.from({ length: 24 }).map((_, i) => (
                <option key={i} value={i}>
                  {i.toString().padStart(2, "0")}:00 UTC
                </option>
              ))}
            </select>
          </div>

          {/* Recipients */}
          <div className="space-y-2">
            <Label>Recipients</Label>
            <div className="flex flex-wrap gap-1">
              {recipients.map((r) => (
                <Badge key={r} variant="default" className="gap-1">
                  {r}
                  <button
                    type="button"
                    onClick={() => removeRecipient(r)}
                    className="rounded-full hover:bg-muted-foreground/10"
                  >
                    <X size={10} />
                  </button>
                </Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={recipientInput}
                onChange={(e) => setRecipientInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    commitRecipient();
                  }
                }}
                placeholder="someone@company.com"
                type="email"
              />
              <Button type="button" variant="outline" onClick={commitRecipient}>
                <Plus size={14} />
              </Button>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={save} disabled={saving || recipients.length === 0}>
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isEdit ? "Save changes" : "Schedule report"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
