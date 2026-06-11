"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { Check, ChevronRight, X } from "lucide-react";
import confetti from "canvas-confetti";
import { useOnboarding } from "@/hooks/use-onboarding";
import { cn } from "@/lib/utils";
import type { OnboardingState } from "@/lib/types";

interface Step {
  key: keyof Pick<
    OnboardingState,
    "workspace_created" | "first_doc_uploaded" | "first_question_asked"
  >;
  label: string;
  cta: string | null;
  href: string | null;
}

const STEPS: Step[] = [
  {
    key: "workspace_created",
    label: "Create your workspace",
    cta: null,
    href: null,
  },
  {
    key: "first_doc_uploaded",
    label: "Upload your first document",
    cta: "Upload",
    href: "/documents?upload=true",
  },
  {
    key: "first_question_asked",
    label: "Ask your first question",
    cta: "Open chat",
    href: "/chat",
  },
];

/**
 * Sidebar checklist for new orgs. Renders until either:
 *   • the caller dismisses it explicitly (X), or
 *   • all three steps complete → confetti fires once → auto-dismiss after 4s.
 *
 * State comes from the backend derived from real doc + message counts, so
 * the checklist self-corrects: deleting every doc un-completes step 2 even
 * after the banner was previously checked, and re-shows the upload CTA.
 */
export function OnboardingChecklist() {
  const { state, dismiss } = useOnboarding();
  const confettiFired = useRef(false);

  useEffect(() => {
    if (!state?.completed) return;
    if (confettiFired.current) return;
    if (state.dismissed) {
      // Already dismissed in a prior session — never fire confetti late.
      confettiFired.current = true;
      return;
    }
    confettiFired.current = true;
    // The canvas position defaults to the document center; aiming a touch
    // above center reads as "burst around the page content" rather than a
    // bottom-up firework.
    confetti({
      particleCount: 120,
      spread: 80,
      origin: { y: 0.4 },
      colors: ["#6366f1", "#8b5cf6", "#06b6d4", "#10b981"],
      disableForReducedMotion: true,
    });
    const t = setTimeout(() => {
      void dismiss();
    }, 4000);
    return () => clearTimeout(t);
  }, [state?.completed, state?.dismissed, dismiss]);

  if (!state) return null;
  if (state.dismissed) return null;

  const completedCount = STEPS.filter((s) => state[s.key]).length;
  const total = STEPS.length;
  const progressPct = (completedCount / total) * 100;

  return (
    <div className="mx-2 my-2 rounded-lg border border-indigo-200 bg-indigo-50/70 p-3 dark:border-indigo-900/60 dark:bg-indigo-950/40">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-indigo-900 dark:text-indigo-100">
            {state.completed ? "You're all set 🎉" : "Get started"}
          </p>
          <p className="mt-0.5 text-[11px] text-indigo-700/80 dark:text-indigo-300/70">
            {completedCount} of {total} done
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            void dismiss();
          }}
          className="-mr-1 -mt-1 rounded p-1 text-indigo-500 hover:bg-indigo-100 hover:text-indigo-700 dark:hover:bg-indigo-900/60"
          aria-label="Dismiss onboarding"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mb-3 h-1 overflow-hidden rounded-full bg-indigo-200 dark:bg-indigo-900/60">
        <div
          className="h-full rounded-full bg-indigo-500 transition-[width] duration-500 ease-out"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <ul className="space-y-1.5">
        {STEPS.map((step) => {
          const done = !!state[step.key];
          return (
            <li key={step.key} className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
                  done
                    ? "border-indigo-500 bg-indigo-500"
                    : "border-indigo-300 dark:border-indigo-700",
                )}
              >
                {done && <Check className="h-2.5 w-2.5 text-white" strokeWidth={3} />}
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-[12px]",
                  done
                    ? "text-indigo-400 line-through dark:text-indigo-600"
                    : "text-indigo-900 dark:text-indigo-100",
                )}
              >
                {step.label}
              </span>
              {!done && step.href && (
                <Link
                  href={step.href}
                  className="inline-flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-indigo-600 hover:underline dark:text-indigo-300"
                >
                  {step.cta}
                  <ChevronRight className="h-3 w-3" />
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
