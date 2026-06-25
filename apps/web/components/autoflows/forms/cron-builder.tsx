"use client";

import { useMemo } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface CronBuilderProps {
  value: string;
  onChange: (next: string) => void;
}

type Preset =
  | "every_minute"
  | "hourly"
  | "daily"
  | "weekly"
  | "monthly"
  | "custom";

const PRESETS: Array<{ id: Preset; label: string; expression: string | null }> = [
  { id: "every_minute", label: "Every minute", expression: "* * * * *" },
  { id: "hourly", label: "Every hour", expression: "0 * * * *" },
  { id: "daily", label: "Daily", expression: null },
  { id: "weekly", label: "Weekly", expression: null },
  { id: "monthly", label: "Monthly", expression: null },
  { id: "custom", label: "Custom cron", expression: null },
];

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function CronBuilder({ value, onChange }: CronBuilderProps) {
  const parsed = useMemo(() => parse(value), [value]);

  const setPreset = (preset: Preset) => {
    if (preset === "every_minute") onChange("* * * * *");
    else if (preset === "hourly") onChange("0 * * * *");
    else if (preset === "daily") onChange("0 9 * * *");
    else if (preset === "weekly") onChange("0 9 * * 1");
    else if (preset === "monthly") onChange("0 9 1 * *");
    else if (preset === "custom") onChange(value || "0 9 * * *");
  };

  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-3">
      <div className="space-y-1.5">
        <Label className="text-xs font-medium">Cadence</Label>
        <Select value={parsed.preset} onValueChange={(p) => setPreset(p as Preset)}>
          <SelectTrigger>
            <SelectValue placeholder="Pick a cadence" />
          </SelectTrigger>
          <SelectContent>
            {PRESETS.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {(parsed.preset === "daily" || parsed.preset === "weekly" || parsed.preset === "monthly") && (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs font-medium">Hour (UTC)</Label>
            <Input
              type="number"
              min={0}
              max={23}
              value={parsed.hour}
              onChange={(e) => onChange(rebuild({ ...parsed, hour: clamp(e.target.value, 0, 23) }))}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs font-medium">Minute</Label>
            <Input
              type="number"
              min={0}
              max={59}
              value={parsed.minute}
              onChange={(e) => onChange(rebuild({ ...parsed, minute: clamp(e.target.value, 0, 59) }))}
            />
          </div>
        </div>
      )}

      {parsed.preset === "weekly" && (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">Day of week</Label>
          <Select
            value={String(parsed.dow)}
            onValueChange={(v) => onChange(rebuild({ ...parsed, dow: Number(v) }))}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {DAYS.map((d, i) => (
                <SelectItem key={i} value={String(i)}>
                  {d}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {parsed.preset === "monthly" && (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">Day of month</Label>
          <Input
            type="number"
            min={1}
            max={28}
            value={parsed.dom}
            onChange={(e) => onChange(rebuild({ ...parsed, dom: clamp(e.target.value, 1, 28) }))}
          />
          <p className="text-[11px] text-muted-foreground">
            Capped at 28 so the flow fires every month.
          </p>
        </div>
      )}

      {parsed.preset === "custom" && (
        <div className="space-y-1.5">
          <Label className="text-xs font-medium">Cron expression</Label>
          <Input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder="0 9 * * 1"
            className="font-mono"
          />
          <p className="text-[11px] text-muted-foreground">
            Standard 5-field cron (min hour dom month dow) in UTC.
          </p>
        </div>
      )}

      <div className="flex items-center gap-2 rounded border border-dashed bg-background p-2 text-[11px]">
        <span className="text-muted-foreground">Cron:</span>
        <code className="font-mono">{value || "—"}</code>
      </div>
    </div>
  );
}

interface ParsedCron {
  preset: Preset;
  minute: number;
  hour: number;
  dom: number;
  dow: number;
}

function parse(v: string): ParsedCron {
  const parts = v.trim().split(/\s+/);
  const base: ParsedCron = { preset: "custom", minute: 0, hour: 9, dom: 1, dow: 1 };
  if (parts.length !== 5) return base;
  const [min, hr, dom, mon, dow] = parts;
  base.minute = parseIntSafe(min, 0);
  base.hour = parseIntSafe(hr, 9);
  base.dom = parseIntSafe(dom, 1);
  base.dow = parseIntSafe(dow, 1);
  if (min === "*" && hr === "*" && dom === "*" && mon === "*" && dow === "*") base.preset = "every_minute";
  else if (min === "0" && hr === "*" && dom === "*" && mon === "*" && dow === "*") base.preset = "hourly";
  else if (isNumeric(min) && isNumeric(hr) && dom === "*" && mon === "*" && dow === "*") base.preset = "daily";
  else if (isNumeric(min) && isNumeric(hr) && dom === "*" && mon === "*" && isNumeric(dow)) base.preset = "weekly";
  else if (isNumeric(min) && isNumeric(hr) && isNumeric(dom) && mon === "*" && dow === "*") base.preset = "monthly";
  else base.preset = "custom";
  return base;
}

function rebuild(p: ParsedCron): string {
  if (p.preset === "every_minute") return "* * * * *";
  if (p.preset === "hourly") return "0 * * * *";
  if (p.preset === "daily") return `${p.minute} ${p.hour} * * *`;
  if (p.preset === "weekly") return `${p.minute} ${p.hour} * * ${p.dow}`;
  if (p.preset === "monthly") return `${p.minute} ${p.hour} ${p.dom} * *`;
  return `${p.minute} ${p.hour} ${p.dom} * ${p.dow}`;
}

function clamp(v: string, lo: number, hi: number): number {
  const n = Number(v);
  if (Number.isNaN(n)) return lo;
  return Math.max(lo, Math.min(hi, Math.round(n)));
}

function isNumeric(s: string): boolean {
  return /^\d+$/.test(s);
}

function parseIntSafe(s: string, fallback: number): number {
  const n = Number(s);
  return Number.isFinite(n) ? n : fallback;
}
