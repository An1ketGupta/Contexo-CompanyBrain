import { Skeleton } from "@/components/ui/skeleton";

/**
 * Skeleton for the multi-card settings page (workspace / profile / team /
 * danger zone). Card heights vary so the layout doesn't look like a grid.
 */
const CARD_FIELDS = [2, 3, 2];

export default function SettingsLoading() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-8">
      <div className="space-y-2">
        <Skeleton className="h-9 w-40" />
        <Skeleton className="h-3.5 w-80" />
      </div>

      {CARD_FIELDS.map((fieldCount, cardIdx) => (
        <div
          key={cardIdx}
          className="space-y-5 rounded-2xl border border-border bg-card p-5"
        >
          <div className="flex items-center justify-between">
            <div className="space-y-1.5">
              <Skeleton className="h-4 w-36" />
              <Skeleton className="h-3 w-56" />
            </div>
            <Skeleton className="h-7 w-7 rounded-lg" />
          </div>
          {Array.from({ length: fieldCount }).map((_, fieldIdx) => (
            <div key={fieldIdx} className="space-y-1.5">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-9 w-full rounded-lg" />
            </div>
          ))}
          <div className="flex justify-end gap-2">
            <Skeleton className="h-8 w-20 rounded-lg" />
            <Skeleton className="h-8 w-24 rounded-lg" />
          </div>
        </div>
      ))}
    </div>
  );
}
