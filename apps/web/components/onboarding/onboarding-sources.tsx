"use client";

/**
 * Job-first onboarding entry point.
 *
 * Renders the published recruiting requisitions an HR team has, plus the
 * candidates synced from their connected ATSes. HR picks a candidate and
 * starts an onboarding run in one click — the StartOnboardingDialog gets
 * pre-filled with identity + role from the recruiting pipeline.
 *
 * "Add manually" fall-throughs are provided at two levels:
 *   - top of the section, for hires that never went through the pipeline
 *   - per-job, when the candidate is e.g. an internal transfer not in ATS
 *
 * Single dialog instance is reused across candidates by swapping its
 * `defaultCandidate` and `requisitionId` props — see start-onboarding-dialog.tsx
 * for the controlled-open API.
 */

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Briefcase,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Plus,
  RefreshCw,
  Sparkles,
  UserPlus,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { StartOnboardingDialog } from "@/components/onboarding/start-onboarding-dialog";

export interface SourceCandidate {
  id: string;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  current_company: string | null;
  current_title: string | null;
  stage: string | null;
  resume_url: string | null;
  candidate_url: string | null;
  applied_at: string | null;
  ats_platform: string | null;
  onboarding_run_id: string | null;
  onboarding_status: string | null;
  looks_hired: boolean;
}

export interface SourceJob {
  id: string;
  role_request: string;
  location: string | null;
  department: string | null;
  seniority_level: string | null;
  published_at: string | null;
  candidates_last_synced_at: string | null;
  notion_tracker_url: string | null;
  candidates: SourceCandidate[];
}

export interface SourcesResponse {
  jobs: SourceJob[];
}

interface OnboardingSourcesProps {
  sources: SourcesResponse | undefined;
  isLoading: boolean;
  error: unknown;
}

type ActiveDialog =
  | { kind: "candidate"; job: SourceJob; candidate: SourceCandidate }
  | { kind: "manual-for-job"; job: SourceJob }
  | { kind: "manual" }
  | null;

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
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

const RUN_STATUS_BLURB: Record<string, string> = {
  draft: "Draft",
  loi_generating: "Generating LOI",
  loi_pending_hr_sign: "Awaiting HR signature",
  loi_signed_uploaded: "LOI signed",
  loi_sent_to_candidate: "LOI sent",
  bgv_pending: "BGV in progress",
  bgv_complete: "BGV complete",
  appointment_bundle_generating: "Bundle generating",
  appointment_pending_hr_review: "Awaiting HR approval",
  appointment_sent_to_candidate: "Offer sent",
  policies_assigned: "Policies assigned",
  policies_acknowledged: "Policies acknowledged",
  induction_generating: "Induction generating",
  induction_sent: "Induction sent",
  completed: "Completed",
  blocked_missing_template: "Blocked",
  failed: "Failed",
};

export function OnboardingSources({
  sources,
  isLoading,
  error,
}: OnboardingSourcesProps) {
  const jobs = sources?.jobs ?? [];

  // Expand the first job by default so the section isn't a wall of closed
  // accordions on first paint. Subsequent jobs the user can toggle manually.
  const initiallyExpanded = useMemo(() => {
    const first = jobs[0];
    return first ? new Set<string>([first.id]) : new Set<string>();
  }, [jobs]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(initiallyExpanded);
  const [active, setActive] = useState<ActiveDialog>(null);

  // Re-sync expanded state when jobs load (handles the SWR loading → loaded
  // transition where initiallyExpanded captures the empty list on first render).
  const expanded =
    expandedIds.size === 0 && jobs.length > 0
      ? new Set<string>([jobs[0].id])
      : expandedIds;

  function toggleJob(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const dialogProps: {
    defaultCandidate?: {
      id?: string;
      name?: string;
      email?: string;
      phone?: string;
    };
    defaultRoleTitle?: string;
    requisitionId?: string;
  } = useMemo(() => {
    if (!active) return {};
    if (active.kind === "candidate") {
      return {
        defaultCandidate: {
          id: active.candidate.id,
          name: active.candidate.full_name ?? undefined,
          email: active.candidate.email ?? undefined,
          phone: active.candidate.phone ?? undefined,
        },
        defaultRoleTitle: active.job.role_request,
        requisitionId: active.job.id,
      };
    }
    if (active.kind === "manual-for-job") {
      return {
        defaultRoleTitle: active.job.role_request,
        requisitionId: active.job.id,
      };
    }
    return {};
  }, [active]);

  return (
    <section className="mb-8 rounded-lg border border-border bg-background">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Sparkles className="h-4 w-4" /> Start a new onboarding
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Pick a candidate from your recruiting pipeline, or add one
            manually for external hires.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setActive({ kind: "manual" })}
        >
          <UserPlus className="mr-1.5 h-3.5 w-3.5" />
          Add manually
        </Button>
      </header>

      <div className="px-4 py-3">
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <p className="text-sm text-red-600">
            Couldn&apos;t load your published jobs. Refresh to retry.
          </p>
        ) : jobs.length === 0 ? (
          <EmptyJobsState />
        ) : (
          <ul className="space-y-2">
            {jobs.map((job) => {
              const isExpanded = expanded.has(job.id);
              return (
                <li
                  key={job.id}
                  className="overflow-hidden rounded-md border border-border bg-muted/20"
                >
                  <button
                    type="button"
                    onClick={() => toggleJob(job.id)}
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-muted/40"
                    aria-expanded={isExpanded}
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <Briefcase className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-foreground">
                        {job.role_request}
                      </p>
                      <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {[job.department, job.location]
                          .filter(Boolean)
                          .join(" · ") || "No location set"}
                        {job.published_at
                          ? ` · Published ${relativeTime(job.published_at)}`
                          : ""}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-full bg-background px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                      {job.candidates.length}{" "}
                      {job.candidates.length === 1
                        ? "candidate"
                        : "candidates"}
                    </span>
                  </button>

                  {isExpanded ? (
                    <div className="border-t border-border bg-background px-3 py-3">
                      <JobCandidatesPanel
                        job={job}
                        onStartCandidate={(c) =>
                          setActive({
                            kind: "candidate",
                            job,
                            candidate: c,
                          })
                        }
                        onAddManual={() =>
                          setActive({ kind: "manual-for-job", job })
                        }
                      />
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <StartOnboardingDialog
        open={active !== null}
        onOpenChange={(o) => {
          if (!o) setActive(null);
        }}
        defaultCandidate={dialogProps.defaultCandidate}
        defaultRoleTitle={dialogProps.defaultRoleTitle}
        requisitionId={dialogProps.requisitionId}
      />
    </section>
  );
}

function EmptyJobsState() {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border bg-muted/20 px-6 py-10 text-center">
      <Briefcase className="mb-2 h-8 w-8 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">
        No published jobs yet
      </p>
      <p className="mt-1 max-w-md text-xs text-muted-foreground">
        Publish a requisition in Recruiting to start sourcing candidates, or
        skip ahead and onboard someone manually.
      </p>
      <Link
        href="/recruiting"
        className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-foreground underline hover:no-underline"
      >
        Open Recruiting <ExternalLink className="h-3 w-3" />
      </Link>
    </div>
  );
}

function JobCandidatesPanel({
  job,
  onStartCandidate,
  onAddManual,
}: {
  job: SourceJob;
  onStartCandidate: (c: SourceCandidate) => void;
  onAddManual: () => void;
}) {
  if (job.candidates.length === 0) {
    return (
      <div className="space-y-3">
        <div className="rounded-md bg-muted/40 px-3 py-3 text-xs text-muted-foreground">
          {job.candidates_last_synced_at ? (
            <>
              No candidates synced yet for this job. Run{" "}
              <Link
                href={`/recruiting/${job.id}`}
                className="font-medium text-foreground underline hover:no-underline"
              >
                Sync candidates
              </Link>{" "}
              in Recruiting to pull applicants from connected ATSes.
            </>
          ) : (
            <>
              No candidates pulled in yet.{" "}
              <Link
                href={`/recruiting/${job.id}`}
                className="font-medium text-foreground underline hover:no-underline"
              >
                Open in Recruiting
              </Link>{" "}
              to sync from your ATSes.
            </>
          )}
        </div>
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Onboarding someone for this role who isn&apos;t in your ATS?
          </p>
          <Button size="sm" variant="outline" onClick={onAddManual}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Add manually for this job
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <ul className="space-y-2">
        {job.candidates.map((c) => (
          <CandidateRow
            key={c.id}
            candidate={c}
            onStart={() => onStartCandidate(c)}
          />
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <RefreshCw className="h-3 w-3" />
          {job.candidates_last_synced_at
            ? `Synced ${relativeTime(job.candidates_last_synced_at)} · `
            : ""}
          <Link
            href={`/recruiting/${job.id}`}
            className="font-medium text-foreground underline hover:no-underline"
          >
            Re-sync in Recruiting
          </Link>
        </p>
        <Button size="sm" variant="outline" onClick={onAddManual}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Add manually
        </Button>
      </div>
    </div>
  );
}

function CandidateRow({
  candidate,
  onStart,
}: {
  candidate: SourceCandidate;
  onStart: () => void;
}) {
  const alreadyOnboarding = Boolean(candidate.onboarding_run_id);
  const statusLabel = candidate.onboarding_status
    ? RUN_STATUS_BLURB[candidate.onboarding_status] ?? candidate.onboarding_status
    : null;

  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border bg-background p-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate text-sm font-medium text-foreground">
            {candidate.full_name || candidate.email || "Unnamed candidate"}
          </p>
          {candidate.looks_hired ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
              <CheckCircle2 className="h-3 w-3" /> Likely hired
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {candidate.email || "no email"}
          {candidate.stage ? ` · ${candidate.stage}` : ""}
          {candidate.current_company
            ? ` · at ${candidate.current_company}`
            : ""}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {candidate.candidate_url ? (
          <a
            href={candidate.candidate_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-muted-foreground underline hover:text-foreground hover:no-underline"
            title="Open in ATS"
          >
            ATS ↗
          </a>
        ) : null}
        {alreadyOnboarding ? (
          <Link
            href={`/onboarding/${candidate.onboarding_run_id}`}
            className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted"
          >
            {statusLabel ?? "Already onboarding"}
            <ExternalLink className="h-3 w-3" />
          </Link>
        ) : (
          <Button
            size="sm"
            onClick={onStart}
            disabled={!candidate.email}
            title={
              candidate.email
                ? undefined
                : "Email missing — add manually or fix in your ATS first"
            }
          >
            Start onboarding
          </Button>
        )}
      </div>
    </li>
  );
}
