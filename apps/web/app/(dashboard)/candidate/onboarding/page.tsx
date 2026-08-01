"use client";

import useSWR from "swr";
import { CheckCircle2, FileText } from "lucide-react";

import {
  CollectUploader,
  type CandidateItem,
} from "@/components/onboarding/collect-uploader";

interface CandidateStep {
  step_key: string;
  label: string;
  bundle_key: string | null;
  bundle_label: string | null;
  status: string;
  items: CandidateItem[];
}

interface CandidateOnboarding {
  run_id: string;
  candidate_name: string;
  company_name: string;
  role_title: string;
  steps: CandidateStep[];
}

const fetcher = async (url: string): Promise<CandidateOnboarding> => {
  const res = await fetch(url);
  if (res.status === 404) throw new Error("no-onboarding");
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

/**
 * What the candidate sees: the documents their new employer has asked for,
 * and a place to upload them.
 *
 * Only steps the run has actually reached come back from the API, so this
 * renders whatever it is given — a step further down the pipeline is absent
 * from the response, not merely hidden here.
 */
export default function CandidateOnboardingPage() {
  const { data, error, isLoading, mutate } = useSWR<CandidateOnboarding>(
    "/api/onboarding/candidate",
    fetcher,
    { revalidateOnFocus: true },
  );

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-10">
        <div className="h-40 animate-pulse rounded-2xl bg-muted" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-10">
        <div className="rounded-2xl border border-border bg-card p-8 text-center">
          <h1 className="text-lg font-bold text-foreground">
            Nothing to do here yet
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {error.message === "no-onboarding"
              ? "You don't have an onboarding in progress. If you're expecting one, check the email your new employer sent you."
              : "We couldn't load your onboarding. Try refreshing the page."}
          </p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const outstanding = data.steps.flatMap((s) =>
    s.items.filter((i) => i.required && !i.submitted),
  );
  const allDone = data.steps.length > 0 && outstanding.length === 0;

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      <header className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-brand">
          {data.company_name}
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-foreground">
          Welcome, {data.candidate_name.split(" ")[0]}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          A few documents before your first day as {data.role_title}.
        </p>
      </header>

      {data.steps.length === 0 ? (
        <div className="rounded-2xl border border-border bg-card p-8 text-center">
          <FileText className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium text-foreground">
            Nothing to upload right now
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            We&apos;ll email you if {data.company_name} needs anything else.
          </p>
        </div>
      ) : (
        <>
          {allDone ? (
            <div className="mb-5 flex items-center gap-3 rounded-xl border border-success bg-success-tint/30 p-4">
              <CheckCircle2 className="h-5 w-5 shrink-0 text-success" />
              <p className="text-sm text-foreground">
                Everything&apos;s in — thanks. You can still replace a file if
                you need to.
              </p>
            </div>
          ) : (
            <p className="mb-5 text-sm text-muted-foreground">
              {outstanding.length} document
              {outstanding.length === 1 ? "" : "s"} still needed.
            </p>
          )}

          <div className="space-y-6">
            {data.steps.map((step) => (
              <section key={step.step_key}>
                <h2 className="mb-2 text-sm font-semibold text-foreground">
                  {step.bundle_label ?? step.label}
                </h2>
                <div className="space-y-2">
                  {step.items.map((item) => (
                    <CollectUploader
                      key={item.item_key}
                      stepKey={step.step_key}
                      item={item}
                      onUploaded={() => mutate()}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
