/**
 * Lightweight chat loading — covers route-segment transitions only. The chat
 * page itself manages its own in-component loading state for SWR-driven data,
 * so this skeleton is intentionally low-effort.
 */
export default function ChatLoading() {
  return (
    <div className="flex h-full">
      <div className="hidden h-full w-64 shrink-0 border-r border-border bg-background/60 md:block">
        <div className="p-3">
          <div className="h-8 w-full animate-pulse rounded-md bg-muted" />
        </div>
        <div className="space-y-1.5 px-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-7 w-full animate-pulse rounded bg-muted/70" />
          ))}
        </div>
      </div>
      <div className="flex flex-1 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-muted-foreground/20 border-t-foreground" />
      </div>
    </div>
  );
}
