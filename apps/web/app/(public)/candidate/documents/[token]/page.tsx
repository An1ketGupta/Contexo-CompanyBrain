"use client";

import { useParams } from "next/navigation";
import useSWR from "swr";
import { CheckCircle2, Clock, FileText, ShieldCheck } from "lucide-react";

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

interface PublicDocuments {
  run_id: string;
  candidate_name: string;
  company_name: string;
  role_title: string;
  steps: CandidateStep[];
  expires_at: string | null;
}

const fetcher = async (url: string): Promise<PublicDocuments> => {
  const res = await fetch(url);
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    throw new Error(
      body?.detail || body?.message || "This link can't be opened.",
    );
  }
  return res.json();
};

function formatExpiry(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/**
 * The candidate's document checklist, opened from the link in their email.
 *
 * No session: the token in the URL is the credential, exactly as the reference
 * forms work. Everything on this page is served by the same code that serves
 * the signed-in portal, so a step the run hasn't reached is absent here too
 * rather than merely hidden.
 */
export default function PublicDocumentsPage() {
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";

  const { data, error, isLoading, mutate } = useSWR<PublicDocuments>(
    token ? `/api/onboarding/public/documents/${token}` : null,
    fetcher,
    { revalidateOnFocus: true },
  );

  if (isLoading) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-12">
        <div className="h-48 animate-pulse rounded-2xl bg-muted" />
      </div>
    );
  }

  if (error) {
    return (
      <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 text-center">
        <Clock className="mb-4 h-10 w-10 text-muted-foreground" />
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          We couldn&apos;t open this upload link
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {error instanceof Error ? error.message : "This link can't be opened."}
        </p>
        <p className="mt-6 text-xs text-muted-foreground">
          Ask the HR contact who emailed you to send a fresh link.
        </p>
      </section>
    );
  }

  if (!data) return null;

  const outstanding = data.steps.flatMap((s) =>
    // A file that was sent back is outstanding again, even though a row for
    // it exists — `submitted` only says something was filed, not that it stood.
    s.items.filter(
      (i) => i.required && (!i.submitted || i.review_status === "rejected"),
    ),
  );
  const inReview = data.steps.filter((s) => s.status === "pending_hr_approval");
  const allDone = data.steps.length > 0 && outstanding.length === 0;
  const expiry = data.expires_at ? formatExpiry(data.expires_at) : null;

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 sm:px-6">
      <header className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-wide text-brand">
          {data.company_name}
        </p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-foreground">
          Hi {data.candidate_name.split(" ")[0]}, we need a few documents
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Upload them here before your first day as {data.role_title}. No
          account or password needed.
        </p>
      </header>

      <div className="mb-6 flex items-start gap-3 rounded-xl border border-border bg-muted/30 p-4">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-foreground" />
        <p className="text-xs text-muted-foreground">
          This link is personal to you — please don&apos;t forward it. Your
          documents go only to {data.company_name}&apos;s HR team.
          {expiry ? ` The link works until ${expiry}.` : ""}
        </p>
      </div>

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
          {allDone && inReview.length ? (
            <div className="mb-5 flex items-center gap-3 rounded-xl border border-border bg-muted/40 p-4">
              <Clock className="h-5 w-5 shrink-0 text-muted-foreground" />
              <p className="text-sm text-foreground">
                Everything&apos;s in and {data.company_name} is checking it now.
                We&apos;ll email you if anything needs another look — nothing to
                do until then.
              </p>
            </div>
          ) : allDone ? (
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
                      // Swapping a file mid-review would mean accepting
                      // something nobody looked at. The server refuses it too.
                      locked={step.status === "pending_hr_approval"}
                      uploadUrl={
                        `/api/onboarding/public/documents/${token}` +
                        `/steps/${encodeURIComponent(step.step_key)}` +
                        `/items/${encodeURIComponent(item.item_key)}`
                      }
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
