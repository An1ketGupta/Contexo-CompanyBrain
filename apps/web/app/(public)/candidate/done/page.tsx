"use client";

import { CheckCircle2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function CandidateDoneContent() {
  const params = useSearchParams();
  const event = params?.get("event") ?? "signing_complete";
  const isDeclined = event === "decline" || event === "declined";

  if (isDeclined) {
    return (
      <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 text-center">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          You&apos;ve declined the offer
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The HR team has been notified. If this was a mistake, please reach
          out to your hiring contact directly.
        </p>
      </section>
    );
  }

  return (
    <section className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 text-center">
      <CheckCircle2 className="mb-4 h-10 w-10 text-emerald-500" />
      <h1 className="text-xl font-semibold tracking-tight text-foreground">
        Thanks for signing.
      </h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Your signed documents are on their way to the HR team. You&apos;ll get
        an email next with a link to acknowledge the company&apos;s policy
        documents before your start date.
      </p>
      <p className="mt-6 text-xs text-muted-foreground">
        You can close this tab.
      </p>
    </section>
  );
}

export default function CandidateDonePage() {
  return (
    <Suspense fallback={null}>
      <CandidateDoneContent />
    </Suspense>
  );
}
