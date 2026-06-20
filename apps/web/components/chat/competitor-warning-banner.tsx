"use client";

import { useState } from "react";
import { ShieldAlert, X } from "lucide-react";
import type { CompetitorMatch } from "@/lib/types";
import { cn } from "@/lib/utils";

interface CompetitorWarningBannerProps {
  matches: CompetitorMatch[];
  className?: string;
}

export function CompetitorWarningBanner({
  matches,
  className,
}: CompetitorWarningBannerProps) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed || matches.length === 0) return null;

  const orgTerms = matches.filter((m) => m.source === "org").map((m) => m.term);
  const userTerms = matches.filter((m) => m.source === "user").map((m) => m.term);

  return (
    <div
      role="alert"
      className={cn(
        "mt-2 flex items-start gap-2.5 rounded-md border border-amber-300/70 bg-amber-50 p-2.5 text-xs text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100",
        className,
      )}
    >
      <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="min-w-0 flex-1 leading-5">
        <div className="font-medium">
          Competitor mention{matches.length > 1 ? "s" : ""} detected
        </div>
        <div className="mt-0.5 text-amber-800 dark:text-amber-200/90">
          This response references{" "}
          {orgTerms.length > 0 && (
            <>
              <span className="font-medium">{orgTerms.join(", ")}</span>
              {orgTerms.length === 1 ? " is" : " are"} on your workspace watchlist
            </>
          )}
          {orgTerms.length > 0 && userTerms.length > 0 && "; "}
          {userTerms.length > 0 && (
            <>
              <span className="font-medium">{userTerms.join(", ")}</span>
              {userTerms.length === 1 ? " is" : " are"} on your personal watchlist
            </>
          )}
          . Review before sharing externally.
        </div>
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="tap -mr-1 -mt-1 inline-flex shrink-0 items-center justify-center rounded p-1 text-amber-700 transition-colors hover:bg-amber-100/70 dark:text-amber-300 dark:hover:bg-amber-500/20"
        aria-label="Dismiss warning"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
