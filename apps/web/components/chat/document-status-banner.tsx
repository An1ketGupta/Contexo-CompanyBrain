"use client";

import Link from "next/link";
import { AlertTriangle, Clock } from "lucide-react";
import { useDocumentStatus } from "@/hooks/use-document-status";

/**
 * Inline warning above the chat input.
 *
 * Three states; only the first two render anything:
 *   1. total=0                        → "knowledge base empty"
 *   2. total>0 ∧ ready=0 ∧ proc>0     → "documents still processing"
 *   3. ready>0                        → hidden (we trust the user to know)
 *
 * Realtime invalidation in `useDocumentStatus` flips state (2) → hidden the
 * moment the first doc finishes processing, with no polling.
 */
export function DocumentStatusBanner() {
  const { status } = useDocumentStatus();
  if (!status) return null;
  if (status.has_ready) return null;

  if (status.total === 0) {
    return (
      <div className="mx-4 mt-3 flex items-start gap-3 rounded-lg border border-amber-300/50 bg-amber-50 px-3 py-2.5 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200 md:mx-6">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">Your knowledge base is empty.</p>
          <p className="mt-0.5 text-xs text-amber-800/80 dark:text-amber-200/70">
            Upload documents first — without them, answers will be generic.
          </p>
        </div>
        <Link
          href="/documents?upload=true"
          className="shrink-0 whitespace-nowrap text-xs font-medium underline underline-offset-2 hover:no-underline"
        >
          Upload now →
        </Link>
      </div>
    );
  }

  // total > 0 but nothing ready yet — pure processing state.
  const n = status.processing;
  return (
    <div className="mx-4 mt-3 flex items-center gap-3 rounded-lg border border-blue-300/50 bg-blue-50 px-3 py-2.5 text-blue-900 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200 md:mx-6">
      <Clock className="h-4 w-4 shrink-0 animate-pulse" />
      <p className="min-w-0 text-sm">
        {n === 1 ? "Your document is" : `Your ${n} documents are`}{" "}
        still being processed — usually 30–60 seconds. Chat will unlock once
        at least one is ready.
      </p>
    </div>
  );
}
