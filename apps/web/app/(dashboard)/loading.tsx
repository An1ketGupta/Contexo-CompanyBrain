/**
 * Generic dashboard loading state. Each segment (chat, documents, settings)
 * can override with its own loading.tsx for a more tailored skeleton.
 */
export default function DashboardLoading() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-muted-foreground/20 border-t-foreground" />
    </div>
  );
}
