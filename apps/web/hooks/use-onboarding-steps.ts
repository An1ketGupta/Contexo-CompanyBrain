"use client";

import useSWR, { mutate as globalMutate } from "swr";

import type { StageKey } from "@/components/onboarding/stage-board";

/**
 * Which optional steps the onboarding pipeline runs for this workspace. LOI is
 * absent because it is never optional.
 */
export interface OnboardingSteps {
  bgv: boolean;
  appointment_bundle: boolean;
  policies: boolean;
  induction: boolean;
  /** False until the org saves a choice — gates the first-run setup screen. */
  configured: boolean;
}

/** The four togglable steps, in pipeline order. */
export const STEP_KEYS = [
  "bgv",
  "appointment_bundle",
  "policies",
  "induction",
] as const;

export type StepKey = (typeof STEP_KEYS)[number];

const KEY = "/api/organizations/me/onboarding-steps";

const ALL_ON: OnboardingSteps = {
  bgv: true,
  appointment_bundle: true,
  policies: true,
  induction: true,
  configured: true,
};

const fetcher = async (url: string): Promise<OnboardingSteps> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

/** The stage keys the board uses, derived from the settings shape. */
export function stagesFromSteps(steps: OnboardingSteps): Set<StageKey> {
  const enabled = new Set<StageKey>(["loi"]);
  if (steps.bgv) enabled.add("bgv");
  if (steps.appointment_bundle) enabled.add("appointment");
  if (steps.policies) enabled.add("policies");
  if (steps.induction) enabled.add("induction");
  return enabled;
}

export function useOnboardingSteps() {
  const { data, error, isLoading } = useSWR(KEY, fetcher, {
    revalidateOnFocus: false,
  });

  // While loading, assume the full pipeline — the same thing the board
  // defaults to, so it doesn't redraw from a wrong shape to a right one.
  // `configured: true` in that fallback matters as much: it keeps the setup
  // screen from flashing over the runs list on every page load.
  const steps = data ?? ALL_ON;

  async function updateSteps(
    patch: Partial<Record<StepKey, boolean>>,
  ): Promise<OnboardingSteps> {
    const res = await fetch(KEY, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || `Failed (${res.status})`);
    }
    const next: OnboardingSteps = await res.json();
    await globalMutate(KEY, next, { revalidate: false });
    return next;
  }

  return {
    steps,
    enabledStages: stagesFromSteps(steps),
    isConfigured: steps.configured,
    isLoading,
    error,
    updateSteps,
  };
}
