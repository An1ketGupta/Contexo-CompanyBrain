"use client";

import { useState } from "react";
import { Check, ThumbsDown, ThumbsUp } from "lucide-react";

import { cn } from "@/lib/utils";

type Vote = "up" | "down" | null;

/**
 * 👍 / 👎 buttons under each help article.
 *
 * Persists locally so a reader who's already voted doesn't see the prompt
 * again, and logs the vote to the analytics endpoint when available. We
 * deliberately don't block on the network — the UI flips immediately and
 * the analytics call is fire-and-forget. If you want server-side persistence
 * later, wire it to /api/help-feedback in the same shape.
 */
export function ArticleFeedback({ slug }: { slug: string }) {
  const storageKey = `help_vote:${slug}`;
  const initial: Vote =
    typeof window !== "undefined"
      ? (window.localStorage.getItem(storageKey) as Vote) ?? null
      : null;
  const [vote, setVote] = useState<Vote>(initial);

  function cast(next: Vote) {
    if (!next || vote === next) return;
    setVote(next);
    try {
      window.localStorage.setItem(storageKey, next);
    } catch {
      // Safari private mode / quota — ignore, the vote is still in state.
    }
    // Best-effort analytics. The endpoint may not exist in dev; we don't
    // want a 404 toast on the page so we swallow failures.
    void fetch("/api/help-feedback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ slug, vote: next }),
    }).catch(() => {});
  }

  if (vote) {
    return (
      <div className="mt-10 flex items-center gap-2 border-t border-border pt-6 text-sm text-muted-foreground">
        <Check className="h-4 w-4 text-emerald-500" />
        Thanks for the feedback.
      </div>
    );
  }

  return (
    <div className="mt-10 border-t border-border pt-6">
      <p className="text-sm text-muted-foreground">Was this helpful?</p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={() => cast("up")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm",
            "transition-colors hover:bg-muted hover:text-foreground text-muted-foreground",
          )}
        >
          <ThumbsUp className="h-3.5 w-3.5" /> Yes
        </button>
        <button
          type="button"
          onClick={() => cast("down")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm",
            "transition-colors hover:bg-muted hover:text-foreground text-muted-foreground",
          )}
        >
          <ThumbsDown className="h-3.5 w-3.5" /> No
        </button>
      </div>
    </div>
  );
}
