import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Tiny shimmer placeholder. Use for any loading state that has a known shape
 * (rows, blocks, avatars). Always pair with a width — otherwise it collapses.
 */
export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}
