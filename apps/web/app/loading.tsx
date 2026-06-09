/**
 * Root-level loading state for routes that haven't defined their own.
 * Kept minimal — a Suspense fallback that won't shift the user's eye while
 * the real UI hydrates.
 */
export default function RootLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted-foreground/20 border-t-foreground" />
        <p className="text-xs text-muted-foreground">Loading…</p>
      </div>
    </div>
  );
}
