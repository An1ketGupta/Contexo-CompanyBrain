"use client";

import Link from "next/link";
import { AlertTriangle, X } from "lucide-react";
import type { KnowledgeGap } from "@/hooks/use-chat";

interface Props {
  gap: KnowledgeGap | null;
  onDismiss: () => void;
}

export function KnowledgeGapBanner({ gap, onDismiss }: Props) {
  if (!gap) return null;

  const topics = (gap.topics ?? [])
    .map((t) => t.trim())
    .filter(Boolean)
    .slice(0, 4);

  return (
    <div
      role="status"
      aria-live="polite"
      className="mb-2 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm dark:border-amber-500/30 dark:bg-amber-500/10"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="flex-1">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          Limited context found
        </p>
        <p className="mt-0.5 text-amber-800/90 dark:text-amber-100/80">
          Your knowledge base didn&apos;t have relevant documents for this task.
          {topics.length > 0 ? (
            <>
              {" "}
              The AI looked for{" "}
              {topics.map((t, i) => (
                <span key={t}>
                  <span className="font-medium">&ldquo;{t}&rdquo;</span>
                  {i < topics.length - 1 ? ", " : null}
                </span>
              ))}
              .
            </>
          ) : null}{" "}
          <Link
            href="/documents"
            className="font-medium text-amber-900 underline-offset-2 hover:underline dark:text-amber-100"
          >
            Upload a document →
          </Link>
        </p>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="rounded-md p-0.5 text-amber-700 transition-colors hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-500/20"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
