import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * Alternating-bubble placeholder shown while a conversation's history is being
 * fetched after a route change. Widths are deliberately uneven so the page
 * doesn't look like a perfect grid before the real messages land.
 */
const ROWS: Array<{ side: "user" | "assistant"; widthClass: string }> = [
  { side: "user", widthClass: "w-3/5" },
  { side: "assistant", widthClass: "w-4/5" },
  { side: "user", widthClass: "w-2/5" },
  { side: "assistant", widthClass: "w-3/4" },
];

export function ChatMessagesSkeleton() {
  return (
    <div className="flex-1 overflow-hidden">
      <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6 md:px-6">
        {ROWS.map((row, i) => (
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
              <Skeleton className="h-3.5 w-4/5 rounded-md" />
              {row.side === "assistant" && (
                <Skeleton className="h-3.5 w-2/3 rounded-md" />
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
