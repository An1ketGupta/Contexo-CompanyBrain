"use client";

import { useEffect } from "react";
import Link from "next/link";
import * as Sentry from "@sentry/nextjs";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Wraps every dashboard page in a React error boundary. The sidebar above
 * us still renders because Next.js doesn't unmount the parent layout — so
 * the user can navigate to another section without a full reload.
 */
export default function DashboardError({
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
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertTriangle className="h-4 w-4" />
        </div>
        <h2 className="text-base font-semibold text-foreground">
          This page hit a snag
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          We logged it. Try again, or jump to another section using the sidebar.
        </p>
        {error.digest && (
          <p className="mt-2 text-xs text-muted-foreground/80">
            Reference: <code className="font-mono">{error.digest}</code>
          </p>
        )}
        <div className="mt-5 flex justify-center gap-2">
          <Button onClick={() => unstable_retry()} size="sm">
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            Try again
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href="/chat">Go to chat</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
