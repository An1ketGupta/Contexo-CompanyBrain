"use client";

import { useState } from "react";
import { Check, Loader2, Lock } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  STEP_KEYS,
  type StepKey,
  type OnboardingSteps,
} from "@/hooks/use-onboarding-steps";

const STEP_COPY: Record<StepKey, { label: string; description: string }> = {
  bgv: {
    label: "Background verification",
    description:
      "Ask the candidate for references, then email each one a verification form and wait for the replies.",
  },
  appointment_bundle: {
    label: "Appointment letter + NDA",
    description:
      "Generate both documents from your templates for HR to review, then send them to the candidate.",
  },
  policies: {
    label: "Policy acknowledgement",
    description:
      "Assign the policies that need signing off and wait for the candidate to acknowledge them.",
  },
  induction: {
    label: "Induction",
    description: "Generate the induction pack and email it to the candidate.",
  },
};

/**
 * First-run screen: the org picks which steps its onboarding runs before it can
 * start one. Shown in place of the runs list until a choice is saved — an org
 * that has never decided and an org that deliberately runs everything look
 * identical in the flags, so the pipeline shouldn't guess which one it is.
 *
 * Everything starts on, so accepting the defaults is one click.
 */
export function OnboardingFlowSetup({
  isAdmin,
  onSave,
  onSaved,
}: {
  isAdmin: boolean;
  onSave: (
    steps: Partial<Record<StepKey, boolean>>,
  ) => Promise<OnboardingSteps>;
  onSaved: () => void;
}) {
  const [selected, setSelected] = useState<Record<StepKey, boolean>>({
    bgv: true,
    appointment_bundle: true,
    policies: true,
    induction: true,
  });
  const [saving, setSaving] = useState(false);

  const chosen = STEP_KEYS.filter((k) => selected[k]).length;

  async function save() {
    setSaving(true);
    try {
      // Send all four, not just the changed ones — this write is what marks
      // the org configured, so it has to be unambiguous about every step.
      await onSave(selected);
      toast.success("Onboarding flow created.");
      onSaved();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Couldn't save your flow.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <div className="rounded-2xl border border-border bg-card">
        <header className="border-b border-border px-6 py-5">
          <h2 className="text-lg font-bold tracking-tight text-foreground">
            Set up your onboarding flow
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            Every company onboards differently. Pick the steps yours runs — the
            agent skips the rest entirely. You can change this later in
            Settings.
          </p>
        </header>

        <div className="space-y-4 px-6 py-5">
          <div className="flex items-start gap-3 rounded-xl border border-dashed border-border bg-muted/30 p-4">
            <Lock className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground">
                Letter of intent
              </p>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Always runs first, and can&apos;t be turned off — every later
                step builds on what it produces.
              </p>
            </div>
          </div>

          {STEP_KEYS.map((key) => {
            const step = STEP_COPY[key];
            const on = selected[key];
            return (
              <button
                key={key}
                type="button"
                role="switch"
                aria-checked={on}
                disabled={!isAdmin || saving}
                onClick={() => setSelected((s) => ({ ...s, [key]: !on }))}
                className={cn(
                  "flex w-full items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                  "disabled:cursor-not-allowed disabled:opacity-60",
                  on
                    ? "border-brand bg-brand-tint/30"
                    : "border-border bg-background hover:bg-muted/40",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border-2 transition-colors",
                    on
                      ? "border-brand bg-brand text-white"
                      : "border-border bg-background",
                  )}
                >
                  {on ? <Check className="h-3 w-3" /> : null}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-foreground">
                    {step.label}
                  </span>
                  <span className="block text-xs leading-relaxed text-muted-foreground">
                    {step.description}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-6 py-4">
          <p className="text-xs text-muted-foreground">
            {isAdmin
              ? `${chosen + 1} step${chosen === 0 ? "" : "s"} selected, including the letter of intent.`
              : "Only workspace admins can set this up."}
          </p>
          <Button onClick={save} disabled={!isAdmin || saving}>
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Creating
              </>
            ) : (
              "Create onboarding flow"
            )}
          </Button>
        </footer>
      </div>
    </div>
  );
}
