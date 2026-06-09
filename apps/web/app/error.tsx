"use client";

/**
 * Root-level error boundary. Covers any segment that doesn't have its own
 * error.tsx (auth pages, marketing). Dashboard has its own at
 * (dashboard)/error.tsx with sidebar-aware styling.
 */
import { useEffect } from "react";
import Link from "next/link";
import * as Sentry from "@sentry/nextjs";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function RootError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">
          Something went wrong
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          A page-level error stopped us from rendering this view. Try again,
          or head back to the dashboard.
        </p>
        {error.digest && (
          <p className="mt-3 text-xs text-muted-foreground/80">
            Reference: <code className="font-mono">{error.digest}</code>
          </p>
        )}
        <div className="mt-6 flex justify-center gap-2">
          <Button onClick={() => unstable_retry()} variant="primary">
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            Try again
          </Button>
          <Button asChild variant="outline">
            <Link href="/chat">Go home</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
