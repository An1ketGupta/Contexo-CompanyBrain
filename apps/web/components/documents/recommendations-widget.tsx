"use client";

import { useMemo, useState } from "react";
import { Check, ChevronDown, ChevronUp, X } from "lucide-react";
import {
  useRecommendations,
  type DocumentRecommendation,
} from "@/hooks/use-recommendations";
import { cn } from "@/lib/utils";

/** V3 #50 — Documents-page checklist of "you'll probably want to upload these".
 *
 *  Visibility rules:
 *   • Hidden entirely if the org has no recommendations (pre-enrichment, or
 *     the post-enrichment Inngest job hasn't run yet).
 *   • Hidden when every entry is either matched or dismissed.
 *   • Auto-collapses (but doesn't hide) once the user has matched at least
 *     half of the visible recommendations — they've gotten the point.
 */
export function RecommendationsWidget() {
  const { recommendations, loading, dismiss } = useRecommendations();
  const [forceOpen, setForceOpen] = useState<boolean | null>(null);

  // Filter once — used by both the visibility gate and the render path.
  const visible = useMemo(
    () => recommendations.filter((r) => !r.dismissed_at),
    [recommendations],
  );

  const totalVisible = visible.length;
  const matchedCount = useMemo(
    () => visible.filter((r) => r.matched_document_id).length,
    [visible],
  );

  if (loading) return null;
  if (totalVisible === 0) return null;

  // Auto-collapse after >= 50% checked. User can still toggle.
  const defaultOpen = matchedCount / totalVisible < 0.5;
  const open = forceOpen ?? defaultOpen;

  const progress = totalVisible === 0 ? 0 : matchedCount / totalVisible;

  return (
    <div className="mb-4 rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setForceOpen(!open)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex-1">
          <p className="text-sm font-semibold text-foreground">
            Recommended documents to upload
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Based on how your team uses Nirnaya IQ · {matchedCount}/{totalVisible}{" "}
            matched
          </p>
        </div>
        <div className="hidden h-1.5 w-24 overflow-hidden rounded-full bg-muted sm:block">
          <div
            className="h-full bg-emerald-500 transition-all"
            style={{ width: `${progress * 100}%` }}
          />
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="border-t border-border px-4 py-3">
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {visible.map((rec) => (
              <RecommendationRow
                key={rec.key}
                rec={rec}
                onDismiss={() => void dismiss(rec.key)}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function RecommendationRow({
  rec,
  onDismiss,
}: {
  rec: DocumentRecommendation;
  onDismiss: () => void;
}) {
  const done = Boolean(rec.matched_document_id);
  return (
    <li
      className={cn(
        "group flex items-start gap-2 rounded-md border border-border px-3 py-2 text-sm",
        done
          ? "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/30 dark:text-emerald-200"
          : "bg-background",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
          done
            ? "border-emerald-500 bg-emerald-500 text-white"
            : "border-muted-foreground/50",
        )}
      >
        {done && <Check className="h-3 w-3" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className={cn("font-medium", done && "line-through")}>{rec.name}</p>
        {rec.description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{rec.description}</p>
        )}
      </div>
      {!done && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDismiss();
          }}
          aria-label={`Dismiss recommendation: ${rec.name}`}
          className="invisible self-start text-muted-foreground/60 hover:text-foreground group-hover:visible"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </li>
  );
}
