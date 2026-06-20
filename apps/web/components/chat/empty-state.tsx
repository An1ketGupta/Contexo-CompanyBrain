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
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Mail className="h-5 w-5" />
      </div>
      <h1 className="text-xl font-semibold tracking-tight text-foreground">
        What should we get done today?
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        Describe any task — an email, a job description, a Slack announcement,
        a policy question. The AI will pull from your company docs.
      </p>

      {!documentsLoading && !hasDocuments && (
        <div className="mt-5 rounded-lg border border-amber-300/40 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          The brain has no knowledge yet.{" "}
          <Link
            href="/documents"
            className="font-medium underline underline-offset-2"
          >
            Upload your first document
          </Link>{" "}
          so it can give grounded answers.
        </div>
      )}
    </div>
  );
}
