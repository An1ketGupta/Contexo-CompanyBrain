"use client";

import { Check, Loader2, Search } from "lucide-react";
import type { SearchProgress } from "@/hooks/use-chat";
import { cn } from "@/lib/utils";

interface SearchingIndicatorProps {
  searches: SearchProgress[];
}

/**
 * Shown above streaming text while the LLM is in its tool-call loop. Each
 * search query lights up "running" then ticks to "done" once results land.
 * The list collapses into a compact summary once tokens start streaming —
 * the parent decides when to render this vs the final text.
 */
export function SearchingIndicator({ searches }: SearchingIndicatorProps) {
  if (searches.length === 0) return null;

  return (
    <div className="mb-3 space-y-1.5 rounded-lg border border-border/70 bg-muted/40 px-3 py-2.5">
      {searches.map((s, i) => (
        <div
          key={`${s.query}-${i}`}
          className="flex items-center gap-2 text-xs text-muted-foreground"
        >
          {s.status === "running" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />
          ) : (
            <Check className="h-3.5 w-3.5 text-success" />
          )}
          <Search className="h-3 w-3 shrink-0 opacity-60" />
          <span
            className={cn(
              "truncate font-medium",
              s.status === "done" && "text-foreground/80",
            )}
          >
            {s.query}
          </span>
          {s.status === "done" && s.hit_count !== null && (
            <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
              {s.hit_count} {s.hit_count === 1 ? "hit" : "hits"}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
