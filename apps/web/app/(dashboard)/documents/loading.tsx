/**
 * Skeleton that mirrors the documents-page layout so there's no jump when
 * real data arrives. Match the dimensions in documents/page.tsx::TableSkeleton.
 */
export default function DocumentsLoading() {
  return (
    <div className="mx-auto max-w-5xl p-6 md:p-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-2">
          <div className="h-5 w-28 animate-pulse rounded bg-muted" />
          <div className="h-3 w-56 animate-pulse rounded bg-muted/70" />
        </div>
        <div className="h-9 w-36 animate-pulse rounded-md bg-muted" />
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-background">
        <div className="border-b border-border bg-muted/40 px-4 py-2.5">
          <div className="h-3 w-20 animate-pulse rounded bg-muted" />
        </div>
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
          >
            <div className="h-4 w-4 animate-pulse rounded bg-muted" />
            <div className="flex-1 space-y-1.5">
              <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
              <div className="h-2.5 w-1/4 animate-pulse rounded bg-muted/70" />
            </div>
            <div className="h-5 w-20 animate-pulse rounded-full bg-muted" />
          </div>
        ))}
      </div>
    </div>
  );
}
