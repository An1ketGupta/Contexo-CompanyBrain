"use client";

import { Mail } from "lucide-react";
import Link from "next/link";

interface EmptyStateProps {
  hasDocuments: boolean;
  documentsLoading?: boolean;
}

export function EmptyState({
  hasDocuments,
  documentsLoading = false,
}: EmptyStateProps) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-tint text-brand">
        <Mail className="h-6 w-6" />
      </div>
      <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
        What should we get done today?
      </h1>
      <p className="mt-2.5 max-w-md text-[15px] leading-relaxed text-muted-foreground">
        Describe any task — an email, a job description, a Slack announcement,
        a policy question. The AI will pull from your company docs.
      </p>

      {!documentsLoading && !hasDocuments && (
        <div className="mt-6 rounded-xl border border-amber/30 bg-amber-tint px-4 py-3 text-sm text-foreground">
          The brain has no knowledge yet.{" "}
          <Link
            href="/documents"
            className="font-semibold text-brand underline-offset-2 hover:underline"
          >
            Upload your first document
          </Link>{" "}
          so it can give grounded answers.
        </div>
      )}
    </div>
  );
}
