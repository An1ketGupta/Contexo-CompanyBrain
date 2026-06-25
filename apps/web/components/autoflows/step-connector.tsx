"use client";

import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";

interface StepConnectorProps {
  onAdd: () => void;
  compact?: boolean;
}

export function StepConnector({ onAdd, compact }: StepConnectorProps) {
  return (
    <div className={cn("group relative flex items-center justify-center", compact ? "h-6" : "h-10")}>
      <div className="absolute inset-x-0 top-1/2 -z-10 mx-auto h-px w-px -translate-y-1/2 border-l border-dashed border-border" style={{ height: compact ? "1.5rem" : "2.5rem" }} />
      <button
        type="button"
        onClick={onAdd}
        aria-label="Add step"
        className="flex h-7 w-7 items-center justify-center rounded-full border border-dashed border-border bg-background text-muted-foreground opacity-50 transition-all hover:scale-110 hover:border-primary hover:text-primary hover:opacity-100 group-hover:opacity-100"
      >
        <Plus className="size-3.5" />
      </button>
    </div>
  );
}
