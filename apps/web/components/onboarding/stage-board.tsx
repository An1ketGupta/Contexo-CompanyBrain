"use client";

import { AlertTriangle, Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The subset of an onboarding run the board reads. Kept structural so the
 * detail page can pass its own `RunDetail` without a cast.
 */
export interface StageBoardRun {
  candidate_name: string;
  role_title: string;
  designation: string | null;
  status: string;
  blocked_reason: string | null;
  blocked_template_kind: string | null;
  loi_sent_to_hr_at: string | null;
  loi_signed_at: string | null;
  bgv_sent_at: string | null;
  bgv_completed_at: string | null;
  appointment_sent_at: string | null;
  policies_assigned_at: string | null;
  policies_acknowledged_at: string | null;
  induction_sent_at: string | null;
  completed_at: string | null;
}

export type StageKey =
  | "loi"
  | "bgv"
  | "appointment"
  | "policies"
  | "induction";

/**
 * The stages an org actually runs. Every stage but LOI can be switched off in
 * settings, and a run then passes through it without stopping — so a disabled
 * stage is dropped from the board rather than shown as permanently "not
 * started". Defaults to all of them, which is what a caller that hasn't loaded
 * the org's settings yet should render.
 */
export type EnabledStages = Set<StageKey>;

const ALL_STAGES: EnabledStages = new Set<StageKey>([
  "loi",
  "bgv",
  "appointment",
  "policies",
  "induction",
]);

const STAGES: {
  key: StageKey;
  label: string;
  /** Abbreviated form, for running the pipeline inline as one line of prose. */
  short: string;
  doneAt: (r: StageBoardRun) => string | null;
}[] = [
  {
    key: "loi",
    label: "LOI",
    short: "LOI",
    doneAt: (r) => r.loi_signed_at ?? r.loi_sent_to_hr_at,
  },
  {
    key: "bgv",
    label: "Background verification",
    short: "BGV",
    doneAt: (r) => r.bgv_completed_at ?? r.bgv_sent_at,
  },
  {
    key: "appointment",
    label: "Appointment + NDA",
    short: "Appointment + NDA",
    doneAt: (r) => r.appointment_sent_at,
  },
  {
    key: "policies",
    label: "Policies",
    short: "Policies",
    doneAt: (r) => r.policies_acknowledged_at ?? r.policies_assigned_at,
  },
  {
    key: "induction",
    label: "Induction",
    short: "Induction",
    doneAt: (r) => r.induction_sent_at,
  },
];

/** Every non-terminal status, mapped to the stage it belongs to. */
const STATUS_STAGE: Record<string, StageKey> = {
  draft: "loi",
  loi_generating: "loi",
  loi_pending_hr_review: "loi",
  loi_pending_hr_sign: "loi",
  loi_pending_esign_signature: "loi",
  loi_signed_uploaded: "loi",
  loi_sent_to_candidate: "loi",
  awaiting_candidate_references: "bgv",
  bgv_pending: "bgv",
  bgv_complete: "bgv",
  appointment_bundle_generating: "appointment",
  appointment_pending_hr_review: "appointment",
  appointment_sent_to_candidate: "appointment",
  policies_assigned: "policies",
  policies_acknowledged: "policies",
  induction_generating: "induction",
  induction_sent: "induction",
};

/** Template kinds the agent can block on, mapped to their stage. */
const TEMPLATE_KIND_STAGE: Record<string, StageKey> = {
  loi: "loi",
  appointment_letter: "appointment",
  appointment: "appointment",
  nda: "appointment",
  policies: "policies",
  induction: "induction",
};

const HALTED = new Set(["blocked_missing_template", "failed", "cancelled"]);

/** Spelled out because Tailwind can't see a class name built at runtime. */
const GRID_COLUMNS: Record<number, string> = {
  1: "lg:grid-cols-1",
  2: "lg:grid-cols-2",
  3: "lg:grid-cols-3",
  4: "lg:grid-cols-4",
  5: "lg:grid-cols-5",
};

/** The stages this org runs, in pipeline order. LOI is never optional. */
export function visibleStages(
  enabled: EnabledStages = ALL_STAGES,
): typeof STAGES {
  return STAGES.filter((s) => s.key === "loi" || enabled.has(s.key));
}

/** The org's pipeline as one arrow-joined line, e.g. `LOI → Policies`. */
export function pipelineSummary(enabled: EnabledStages = ALL_STAGES): string {
  return visibleStages(enabled)
    .map((s) => s.short)
    .join(" → ");
}

/**
 * Which stage the run is sitting in, before any filtering.
 *
 * Blocked and failed runs carry no stage in their status, so they fall back to
 * the template kind the agent choked on, then to the timestamps the run has
 * already written.
 */
function resolveStage(run: StageBoardRun): StageKey {
  const fromStatus = STATUS_STAGE[run.status];
  if (fromStatus !== undefined) return fromStatus;

  if (HALTED.has(run.status) && run.blocked_template_kind) {
    const fromTemplate = TEMPLATE_KIND_STAGE[run.blocked_template_kind];
    if (fromTemplate !== undefined) return fromTemplate;
  }

  if (run.induction_sent_at) return "induction";
  if (run.policies_assigned_at) return "policies";
  if (run.appointment_sent_at) return "appointment";
  if (run.bgv_sent_at) return "bgv";
  return "loi";
}

/**
 * Which column the candidate box sits in, counted against the stages this org
 * actually runs.
 *
 * `completed` returns past the last index so every column reads as done.
 */
export function currentStageIndex(
  run: StageBoardRun,
  enabled: EnabledStages = ALL_STAGES,
): number {
  const stages = visibleStages(enabled);
  if (run.status === "completed") return stages.length;

  const key = resolveStage(run);
  const i = stages.findIndex((s) => s.key === key);
  if (i !== -1) return i;

  // The run is in a stage this org no longer runs — it started before the
  // stage was switched off. Count the visible stages it has already cleared,
  // which lands the box on the next one it will actually stop at.
  const canonical = STAGES.findIndex((s) => s.key === key);
  return stages.filter(
    (s) => STAGES.findIndex((x) => x.key === s.key) < canonical,
  ).length;
}

/**
 * The stage whose panel the detail page shows. A completed run indexes past the
 * last stage, so it clamps back onto the final one rather than falling off the
 * end.
 */
export function currentStageKey(
  run: StageBoardRun,
  enabled: EnabledStages = ALL_STAGES,
): StageKey {
  const stages = visibleStages(enabled);
  const i = Math.min(currentStageIndex(run, enabled), stages.length - 1);
  return stages[i].key;
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
 * The stages this org runs as columns, with the candidate box parked in
 * whichever column the run is sitting in. The page shows only the current
 * stage's panel below, so the columns are a status readout rather than
 * navigation.
 */
export function StageBoard({
  run,
  statusLabel,
  enabledStages = ALL_STAGES,
}: {
  run: StageBoardRun;
  statusLabel: string;
  enabledStages?: EnabledStages;
}) {
  const stages = visibleStages(enabledStages);
  const current = currentStageIndex(run, enabledStages);
  const halted = HALTED.has(run.status);

  return (
    <ol
      className={cn(
        "grid grid-cols-1 gap-4 sm:grid-cols-2",
        GRID_COLUMNS[stages.length],
      )}
    >
      {stages.map((stage, i) => {
        const isDone = i < current;
        const isCurrent = i === current;
        const doneAt = relativeTime(stage.doneAt(run));

        return (
          <li
            key={stage.key}
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
                title={stage.label}
              >
                {stage.label}
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
