"use client";

import { AlertTriangle, Check } from "lucide-react";

import type { RunStep } from "@/components/onboarding/step-panel";
import { cn } from "@/lib/utils";

/**
 * The subset of an onboarding run the board reads. Kept structural so the
 * detail page can pass its own `RunDetail` without a cast.
 *
 * Only the candidate's identity and whether the run itself has been halted —
 * everything about *where* the run has got to comes from its steps. This used
 * to also read five pairs of legacy timestamp columns (`loi_signed_at`,
 * `bgv_sent_at`, …) because the board's columns were the five stages that used
 * to be hardcoded. They are the org's steps now, so the run row has nothing
 * left to say about progress.
 */
export interface StageBoardRun {
  candidate_name: string;
  role_title: string;
  designation: string | null;
  status: string;
}

/** A step in one of these needs nothing further — mirrors the agent's set. */
const TERMINAL = new Set(["done", "skipped"]);

/** A step that has stopped and needs someone to intervene. */
const HALTED_STEP = new Set([
  "blocked_missing_template",
  "blocked_template_drift",
  "failed",
]);

/** A run that has stopped, whatever its steps say. */
const HALTED_RUN = new Set(["failed", "cancelled"]);

/**
 * Spelled out because Tailwind can't see a class name built at runtime. A
 * pipeline longer than five wraps at five rather than squeezing an arbitrary
 * number of columns onto one row — the numbering still reads in order.
 */
const GRID_COLUMNS: Record<number, string> = {
  1: "lg:grid-cols-1",
  2: "lg:grid-cols-2",
  3: "lg:grid-cols-3",
  4: "lg:grid-cols-4",
  5: "lg:grid-cols-5",
};

/**
 * This run's pipeline as board columns: one per step, bundle members sharing
 * one, in position order.
 *
 * Skipped steps are dropped: a column reading "Not run" is a column spent on
 * nothing.
 */
export function stageGroups(steps: RunStep[]): RunStep[][] {
  const groups: RunStep[][] = [];
  const seen = new Set<string>();
  for (const step of [...steps].sort((a, b) => a.position - b.position)) {
    if (step.status === "skipped") continue;
    if (!step.bundle_key) {
      groups.push([step]);
      continue;
    }
    if (seen.has(step.bundle_key)) continue;
    seen.add(step.bundle_key);
    groups.push(
      steps
        .filter((s) => s.bundle_key === step.bundle_key && s.status !== "skipped")
        .sort((a, b) => a.position - b.position),
    );
  }
  return groups;
}

/**
 * The column the run is sitting in — the first group that still needs
 * something, in pipeline order. Null means every step is done.
 *
 * This is the same rule the agent dispatches on (`catalog.next_actionable`),
 * which is the point: the board shows where the run is because it asks the
 * same question of the same rows, rather than inferring a stage from a status
 * string and a spread of timestamps that could disagree with it.
 *
 * A group, not a step, because a bundle is reviewed and signed as one thing —
 * the panel for it has to name every document in it.
 */
export function currentStageGroup(steps: RunStep[]): RunStep[] | null {
  for (const group of stageGroups(steps)) {
    if (!TERMINAL.has(group[0].status)) return group;
  }
  return null;
}

/**
 * The group whose panel the run page shows.
 *
 * The one the run is sitting in, or the last one once it has finished — a
 * completed hire should still be able to open the documents its pipeline
 * produced, which is what the old board's clamp past the final stage did.
 */
export function panelStageGroup(steps: RunStep[]): RunStep[] | null {
  const groups = stageGroups(steps);
  return currentStageGroup(steps) ?? groups[groups.length - 1] ?? null;
}

function relativeTime(iso: string | null): string | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * The steps this run is walking as columns, with the candidate box parked in
 * whichever one it is sitting in. The page shows only the current step's panel
 * below, so the columns are a status readout rather than navigation.
 */
export function StageBoard({
  run,
  steps,
  statusLabel,
}: {
  run: StageBoardRun;
  steps: RunStep[];
  statusLabel: string;
}) {
  const groups = stageGroups(steps);
  if (groups.length === 0) return null;

  const current = currentStageGroup(steps);
  // No current step means every one is done, so every column reads as done.
  const currentIndex = current
    ? groups.findIndex((g) => g[0].id === current[0].id)
    : groups.length;
  const runHalted = HALTED_RUN.has(run.status);

  return (
    <ol
      className={cn(
        "grid grid-cols-1 gap-4 sm:grid-cols-2",
        GRID_COLUMNS[groups.length] ?? "lg:grid-cols-5",
      )}
    >
      {groups.map((group, i) => {
        const lead = group[0];
        const label = lead.bundle_label ?? lead.label;
        const isDone = i < currentIndex;
        const isCurrent = i === currentIndex;
        const halted = runHalted || HALTED_STEP.has(lead.status);
        const doneAt = relativeTime(lead.completed_at);

        return (
          <li
            key={lead.id}
            className={cn(
              "flex h-full flex-col rounded-2xl border p-4",
              isCurrent && halted
                ? "border-destructive/40 bg-destructive-soft"
                : isCurrent
                  ? "border-brand bg-brand-tint/40 ring-1 ring-brand/30"
                  : isDone
                    ? "border-border bg-card"
                    : "border-dashed border-border bg-background",
            )}
          >
            <div className="mb-3 flex items-center gap-2">
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold",
                  isDone
                    ? "bg-success-tint text-success"
                    : isCurrent && halted
                      ? "bg-destructive text-white"
                      : isCurrent
                        ? "bg-brand text-white"
                        : "bg-muted text-muted-foreground",
                )}
              >
                {isDone ? <Check className="h-3 w-3" /> : i + 1}
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate font-mono text-[11px] font-bold uppercase tracking-wider",
                  isCurrent ? "text-foreground" : "text-muted-foreground",
                )}
                title={label}
              >
                {label}
              </span>
            </div>

            <div className="flex min-h-[124px] flex-1 flex-col justify-center">
              {isCurrent ? (
                <div
                  className={cn(
                    "rounded-xl border bg-card p-3 shadow-sm",
                    halted ? "border-destructive/40" : "border-brand/40",
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className={cn(
                        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                        halted
                          ? "bg-destructive-soft text-destructive"
                          : "bg-brand-tint text-brand",
                      )}
                    >
                      {initials(run.candidate_name)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-bold text-foreground">
                        {run.candidate_name}
                      </span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {run.designation || run.role_title}
                      </span>
                    </span>
                  </div>
                  <p
                    className={cn(
                      "mt-2.5 flex items-start gap-1 text-[11px] font-semibold leading-snug",
                      halted ? "text-destructive" : "text-brand",
                    )}
                  >
                    {halted ? (
                      <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" />
                    ) : null}
                    <span className="min-w-0">{statusLabel}</span>
                  </p>
                </div>
              ) : isDone ? (
                <div className="text-center">
                  <Check className="mx-auto h-5 w-5 text-success" />
                  <p className="mt-1.5 text-[11px] font-semibold text-muted-foreground">
                    {doneAt ? `Done · ${doneAt}` : "Done"}
                  </p>
                </div>
              ) : (
                <p className="text-center text-[11px] text-muted-foreground/60">
                  Not started
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
