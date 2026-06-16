import { Skeleton } from "@/components/ui/skeleton";

/**
 * Generic dashboard content-area skeleton. The persistent sidebar comes from
 * the layout, so this only renders inside the scroll container. Each segment
 * (chat, documents, settings) overrides with its own tailored skeleton.
 */
export default function DashboardLoading() {
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <div className="space-y-2.5">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-3.5 w-72" />
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="space-y-2 rounded-lg border border-border bg-background p-4"
          >
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-7 w-20" />
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-background">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 border-b border-border px-4 py-3.5 last:border-b-0"
          >
            <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3.5 w-2/5" />
              <Skeleton className="h-2.5 w-1/4" />
            </div>
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
