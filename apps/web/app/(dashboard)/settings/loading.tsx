/**
 * Skeleton for the three-card settings layout (workspace / profile / danger).
 */
export default function SettingsLoading() {
  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-8">
      <div className="space-y-2">
        <div className="h-5 w-20 animate-pulse rounded bg-muted" />
        <div className="h-3 w-72 animate-pulse rounded bg-muted/70" />
      </div>

      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="space-y-4 rounded-lg border border-border bg-background p-5"
        >
          <div className="h-4 w-32 animate-pulse rounded bg-muted" />
          <div className="space-y-2">
            <div className="h-3 w-16 animate-pulse rounded bg-muted/70" />
            <div className="h-9 w-full animate-pulse rounded-md bg-muted" />
          </div>
          <div className="space-y-2">
            <div className="h-3 w-16 animate-pulse rounded bg-muted/70" />
            <div className="h-9 w-full animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex justify-end">
            <div className="h-8 w-28 animate-pulse rounded-md bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
