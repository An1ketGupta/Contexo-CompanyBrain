"use client";

import { useEffect } from "react";
import { ShieldAlert } from "lucide-react";
import type { CompetitorMatch } from "@/lib/types";

interface CompetitorConfirmDialogProps {
  matches: CompetitorMatch[];
  /** Friendly destination name — "Gmail", "Slack", "Notion", "Google Docs", "approval". */
  destination: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function CompetitorConfirmDialog({
  matches,
  destination,
  onCancel,
  onConfirm,
}: CompetitorConfirmDialogProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const terms = Array.from(new Set(matches.map((m) => m.term)));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="competitor-confirm-title"
    >
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-xl">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-500/15">
            <ShieldAlert className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          </div>
          <div className="min-w-0 flex-1">
            <h2
              id="competitor-confirm-title"
              className="text-sm font-semibold text-foreground"
            >
              Competitor mention in this {destination} send
            </h2>
            <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
              This content references{" "}
              <span className="font-medium text-foreground">
                {terms.join(", ")}
              </span>
              , which {terms.length === 1 ? "is" : "are"} on your watchlist.
              Double-check before it leaves the workspace.
            </p>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-amber-700 focus:outline-none focus:ring-2 focus:ring-amber-500"
          >
            Send anyway
          </button>
        </div>
      </div>
    </div>
  );
}
