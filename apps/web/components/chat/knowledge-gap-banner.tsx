"use client";

import { AlertCircle, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { KnowledgeGap } from "@/hooks/use-chat";

interface KnowledgeGapBannerProps {
  gap: KnowledgeGap | null;
  onDismiss: () => void;
}

export function KnowledgeGapBanner({ gap, onDismiss }: KnowledgeGapBannerProps) {
  if (!gap) return null;

  return (
    <div className="mb-2 flex items-start gap-2 rounded-md border border-amber-300/60 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">Knowledge gap detected</p>
        <p className="mt-1 text-xs opacity-90">
          These topics weren&apos;t found in your knowledge base:{" "}
          {gap.topics.join(", ")}
        </p>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 w-6 shrink-0 p-0"
        onClick={onDismiss}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
