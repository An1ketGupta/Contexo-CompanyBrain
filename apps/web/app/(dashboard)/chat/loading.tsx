import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Chat route skeleton — conversation list rail + alternating message bubbles
 * + composer. Matches the real layout so there's no jump on hydration.
 */
const BUBBLES: Array<{ side: "user" | "assistant"; widthClass: string }> = [
  { side: "assistant", widthClass: "w-4/5" },
  { side: "user", widthClass: "w-3/5" },
  { side: "assistant", widthClass: "w-3/4" },
  { side: "user", widthClass: "w-2/5" },
];

export default function ChatLoading() {
  return (
    <div className="flex h-full">
      <aside className="hidden h-full w-64 shrink-0 border-r border-border bg-background/60 md:flex md:flex-col">
        <div className="border-b border-border p-3">
          <Skeleton className="h-9 w-full" />
        </div>
        <div className="flex-1 space-y-1 p-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="flex items-center gap-2 px-2 py-1.5">
              <Skeleton className="h-3.5 w-3.5 shrink-0 rounded" />
              <Skeleton
                className="h-3 flex-1"
                style={{ maxWidth: `${60 + ((i * 17) % 35)}%` }}
              />
            </div>
          ))}
        </div>
      </aside>

      <main className="flex flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-border px-4 py-3 md:px-6">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-7 w-7 rounded-md" />
        </div>

        <div className="flex-1 overflow-hidden">
          <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-8 md:px-6">
            {BUBBLES.map((row, i) => (
              <div
                key={i}
                className={cn(
                  "flex gap-3",
                  row.side === "user" ? "justify-end" : "justify-start",
                )}
              >
                {row.side === "assistant" && (
                  <Skeleton className="h-7 w-7 shrink-0 rounded-full" />
                )}
                <div className={cn("flex flex-col gap-1.5", row.widthClass)}>
                  <Skeleton className="h-3.5 w-full rounded-md" />
                  <Skeleton className="h-3.5 w-5/6 rounded-md" />
                  {row.side === "assistant" && (
                    <Skeleton className="h-3.5 w-3/5 rounded-md" />
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="border-t border-border bg-background px-4 py-3 md:px-6">
          <div className="mx-auto max-w-3xl">
            <Skeleton className="h-11 w-full rounded-lg" />
          </div>
        </div>
      </main>
    </div>
  );
}
