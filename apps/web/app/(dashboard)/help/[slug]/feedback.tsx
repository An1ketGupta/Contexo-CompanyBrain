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
      <div className="mt-10 flex items-start gap-3 rounded-xl border border-success-ink/20 bg-success-tint p-4">
        <Check className="mt-0.5 size-4 shrink-0 text-success-ink" />
        <p className="text-sm font-semibold text-success-ink">Thanks for the feedback.</p>
      </div>
    );
  }

  return (
    <div className="mt-10 border-t border-border pt-6">
      <p className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
        Was this helpful?
      </p>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => cast("up")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border border-border px-4 py-1.5 text-sm font-semibold",
            "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          )}
        >
          <ThumbsUp className="size-3.5" /> Yes
        </button>
        <button
          type="button"
          onClick={() => cast("down")}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border border-border px-4 py-1.5 text-sm font-semibold",
            "text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          )}
        >
          <ThumbsDown className="size-3.5" /> No
        </button>
      </div>
    </div>
  );
}
