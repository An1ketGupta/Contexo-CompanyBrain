import { Skeleton } from "@/components/ui/skeleton";

/**
 * Mirrors the documents page so the user's eye lands in the same place when
 * the real table renders. Dimensions track documents/page.tsx::TableSkeleton.
 */
export default function DocumentsLoading() {
  return (
    <div className="mx-auto max-w-5xl p-6 md:p-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-2">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-3 w-64" />
        </div>
        <Skeleton className="h-9 w-36 rounded-md" />
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        <Skeleton className="h-8 w-64 rounded-md" />
        <Skeleton className="h-8 w-28 rounded-md" />
        <Skeleton className="h-8 w-28 rounded-md" />
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-background">
        <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-4 py-2.5">
          <Skeleton className="h-3 w-16" />
          <div className="ml-auto flex gap-4">
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-16" />
          </div>
        </div>
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
          >
            <Skeleton className="h-4 w-4 rounded" />
            <Skeleton className="h-4 w-4 rounded" />
            <div className="flex-1 space-y-1.5">
              <Skeleton
                className="h-3.5"
                style={{ width: `${45 + ((i * 13) % 40)}%` }}
              />
              <Skeleton className="h-2.5 w-1/4" />
            </div>
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-3 w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}
