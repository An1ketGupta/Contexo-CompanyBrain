"use client";

import Link from "next/link";
import { ChevronLeft, Loader2, Play, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import type { AutoflowDraft } from "@/lib/autoflow/types";
import { ValidationResult } from "./validation";

interface BuilderToolbarProps {
  draft: AutoflowDraft;
  onUpdate: (next: AutoflowDraft) => void;
  validation: ValidationResult;
  saving: boolean;
  onSave: () => void;
  onTest?: () => void;
  testDisabled?: boolean;
  backHref: string;
  saveLabel?: string;
  rightSlot?: React.ReactNode;
}

export function BuilderToolbar({
  draft,
  onUpdate,
  validation,
  saving,
  onSave,
  onTest,
  testDisabled,
  backHref,
  saveLabel = "Save",
  rightSlot,
}: BuilderToolbarProps) {
  return (
    <div className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 lg:px-6">
        <Button asChild variant="ghost" size="sm" className="gap-1">
          <Link href={backHref}>
            <ChevronLeft className="size-4" />
            Back
          </Link>
        </Button>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Input
            value={draft.name}
            onChange={(e) => onUpdate({ ...draft, name: e.target.value })}
            placeholder="Untitled autoflow"
            className="h-8 max-w-md text-sm font-medium"
          />
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8 text-xs text-muted-foreground">
                {draft.description ? "Description" : "+ Description"}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-80">
              <Textarea
                rows={4}
                value={draft.description ?? ""}
                onChange={(e) => onUpdate({ ...draft, description: e.target.value || null })}
                placeholder="What does this autoflow do?"
                className="text-sm"
              />
            </PopoverContent>
          </Popover>
          <Badge
            variant={draft.is_active ? "default" : "outline"}
            className="cursor-pointer"
            onClick={() => onUpdate({ ...draft, is_active: !draft.is_active })}
            title="Toggle active"
          >
            {draft.is_active ? "Active" : "Inactive"}
          </Badge>
        </div>

        {!validation.ok && (
          <Popover>
            <PopoverTrigger asChild>
              <Button variant="ghost" size="sm" className="text-xs text-destructive-ink">
                {validation.errors.length} issue{validation.errors.length === 1 ? "" : "s"}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80">
              <ul className="space-y-1 text-xs text-destructive-ink">
                {validation.errors.map((e, i) => (
                  <li key={i}>• {e}</li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>
        )}

        {rightSlot}

        {onTest && (
          <Button
            onClick={onTest}
            variant="outline"
            size="sm"
            disabled={testDisabled}
            className="gap-1"
          >
            <Play className="size-3.5" />
            Test
          </Button>
        )}

        <Button
          onClick={onSave}
          disabled={saving || !validation.ok}
          size="sm"
          className="gap-1"
        >
          {saving ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Save className="size-3.5" />
          )}
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}
