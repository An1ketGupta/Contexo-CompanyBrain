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

const STAGES: {
  key: StageKey;
  label: string;
  doneAt: (r: StageBoardRun) => string | null;
}[] = [
  {
    key: "loi",
    label: "LOI",
    doneAt: (r) => r.loi_signed_at ?? r.loi_sent_to_hr_at,
  },
  {
    key: "bgv",
    label: "Background verification",
    doneAt: (r) => r.bgv_completed_at ?? r.bgv_sent_at,
  },
  {
    key: "appointment",
    label: "Appointment + NDA",
    doneAt: (r) => r.appointment_sent_at,
  },
  {
    key: "policies",
    label: "Policies",
    doneAt: (r) => r.policies_acknowledged_at ?? r.policies_assigned_at,
  },
  { key: "induction", label: "Induction", doneAt: (r) => r.induction_sent_at },
];

/** Every non-terminal status, mapped to the column it belongs in. */
const STATUS_STAGE: Record<string, number> = {
  draft: 0,
  loi_generating: 0,
  loi_pending_hr_review: 0,
  loi_pending_hr_sign: 0,
  loi_pending_esign_signature: 0,
  loi_signed_uploaded: 0,
  loi_sent_to_candidate: 0,
  awaiting_candidate_references: 1,
  bgv_pending: 1,
  bgv_complete: 1,
  appointment_bundle_generating: 2,
  appointment_pending_hr_review: 2,
  appointment_sent_to_candidate: 2,
  policies_assigned: 3,
  policies_acknowledged: 3,
  induction_generating: 4,
  induction_sent: 4,
};

/** Template kinds the agent can block on, mapped to their column. */
const TEMPLATE_KIND_STAGE: Record<string, number> = {
  loi: 0,
  appointment_letter: 2,
  appointment: 2,
  nda: 2,
  policies: 3,
  induction: 4,
};

const HALTED = new Set(["blocked_missing_template", "failed", "cancelled"]);

/**
 * Which column the candidate box sits in.
 *
 * `completed` returns past the last index so every column reads as done.
 * Blocked and failed runs carry no stage in their status, so they fall back to
 * the template kind the agent choked on, then to the timestamps the run has
 * already written.
 */
export function currentStageIndex(run: StageBoardRun): number {
  if (run.status === "completed") return STAGES.length;

  const fromStatus = STATUS_STAGE[run.status];
  if (fromStatus !== undefined) return fromStatus;

  if (HALTED.has(run.status)) {
    const fromTemplate = run.blocked_template_kind
      ? TEMPLATE_KIND_STAGE[run.blocked_template_kind]
      : undefined;
    if (fromTemplate !== undefined) return fromTemplate;
  }

  if (run.induction_sent_at) return 4;
  if (run.policies_assigned_at) return 3;
  if (run.appointment_sent_at) return 2;
  if (run.bgv_sent_at) return 1;
  return 0;
}

/**
 * The stage whose panel the detail page shows. A completed run indexes past the
 * last stage, so it clamps back onto Induction rather than falling off the end.
 */
export function currentStageKey(run: StageBoardRun): StageKey {
  const i = Math.min(currentStageIndex(run), STAGES.length - 1);
  return STAGES[i].key;
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
 * The run's five stages as columns, with the candidate box parked in whichever
 * column the run is sitting in. The page shows only the current stage's panel
 * below, so the columns are a status readout rather than navigation.
 */
export function StageBoard({
  run,
  statusLabel,
}: {
  run: StageBoardRun;
  statusLabel: string;
}) {
  const current = currentStageIndex(run);
  const halted = HALTED.has(run.status);

  return (
    <ol className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
      {STAGES.map((stage, i) => {
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
