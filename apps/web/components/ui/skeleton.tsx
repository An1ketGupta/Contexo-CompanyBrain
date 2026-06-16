import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Content-shaped loading placeholder. A soft gradient sweep (shimmer) runs
 * over a muted block — looks "loading real data" instead of a flat pulse.
 * Always pair with explicit width/height — otherwise it collapses to zero.
 *
 * Honours `prefers-reduced-motion` (animation disabled).
 */
export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-skeleton
      className={cn(
        "relative isolate overflow-hidden rounded-md bg-muted",
        "before:absolute before:inset-0 before:-translate-x-full",
        "before:animate-[shimmer_1.6s_ease-in-out_infinite]",
        "before:bg-gradient-to-r before:from-transparent before:via-black/[0.05] before:to-transparent",
        "dark:before:via-white/[0.07]",
        className,
      )}
      {...props}
    />
  );
}
