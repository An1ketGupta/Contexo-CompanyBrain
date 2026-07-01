import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Actual design-system page atoms. Shared by the work-execution surfaces
 * (Sequences, Pre-call brief, RFP responses, Briefs) so the eyebrow → h1 → sub
 * header, mono stat cards, and dotted status pills stay identical across them.
 */

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="space-y-2">
        {eyebrow ? (
          <p className="text-[13px] font-bold text-brand">{eyebrow}</p>
        ) : null}
        <h1 className="text-3xl font-extrabold tracking-tight">{title}</h1>
        {description ? (
          <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </header>
  );
}

export function StatGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = "flat",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "up" | "down" | "flat";
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="mt-2.5 text-[28px] font-extrabold leading-none tracking-tight">
        {value}
      </p>
      {hint ? (
        <p
          className={cn(
            "mt-2 text-xs font-bold",
            tone === "up" && "text-success",
            tone === "down" && "text-destructive",
            tone === "flat" && "text-muted-foreground",
          )}
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export type PillTone =
  | "green"
  | "amber"
  | "blue"
  | "violet"
  | "red"
  | "gray";

const PILL_TONE: Record<PillTone, string> = {
  green: "bg-success-tint text-success",
  amber: "bg-amber-tint text-amber",
  blue: "bg-brand-tint text-brand",
  violet: "bg-violet-tint text-violet",
  red: "bg-destructive-soft text-destructive",
  gray: "bg-muted text-muted-foreground",
};

/** Pill badge with a leading status dot (Actual `.badge` + `.dot`). */
export function StatusPill({
  tone = "gray",
  children,
  className,
}: {
  tone?: PillTone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-bold",
        PILL_TONE[tone],
        className,
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}
