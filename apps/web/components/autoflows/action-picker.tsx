"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { ACTION_CATALOG, ACTION_CATEGORIES } from "@/lib/autoflow/catalog";
import { getIcon } from "@/lib/autoflow/icons";
import type { ActionType } from "@/lib/autoflow/types";

interface ActionPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (type: ActionType) => void;
}

const CATEGORY_STYLES = {
  ai: "bg-violet-tint text-violet",
  notify: "bg-brand-tint text-brand",
  integrations: "bg-success-tint text-success-ink",
  control: "bg-amber-tint text-amber-ink",
};

export function ActionPicker({ open, onOpenChange, onPick }: ActionPickerProps) {
  const [query, setQuery] = useState("");

  const filtered = ACTION_CATALOG.filter((a) => {
    if (!query) return true;
    const q = query.toLowerCase();
    return (
      a.label.toLowerCase().includes(q) ||
      a.shortLabel.toLowerCase().includes(q) ||
      a.description.toLowerCase().includes(q)
    );
  });

  const handlePick = (type: ActionType) => {
    onPick(type);
    onOpenChange(false);
    setQuery("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add an action</DialogTitle>
        </DialogHeader>
        <div className="relative">
          <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search actions…"
            className="pl-9"
            autoFocus
          />
        </div>

        <div className="max-h-[55vh] space-y-5 overflow-y-auto pr-1">
          {ACTION_CATEGORIES.map((cat) => {
            const items = filtered.filter((a) => a.category === cat.id);
            if (!items.length) return null;
            return (
              <div key={cat.id}>
                <div className="mb-2 font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                  {cat.label}
                </div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {items.map((a) => {
                    const Icon = getIcon(a.icon);
                    const disabled = !a.available;
                    return (
                      <button
                        key={a.type}
                        type="button"
                        disabled={disabled}
                        onClick={() => handlePick(a.type)}
                        className={cn(
                          "group flex items-start gap-3 rounded-2xl border border-border bg-card p-3 text-left transition-all",
                          disabled
                            ? "cursor-not-allowed opacity-50"
                            : "hover:border-brand hover:shadow-md",
                        )}
                      >
                        <div
                          className={cn(
                            "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl",
                            CATEGORY_STYLES[a.category],
                          )}
                        >
                          <Icon className="size-4" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-semibold">{a.label}</p>
                            {!a.available && (
                              <Badge variant="outline" className="text-[10px]">Soon</Badge>
                            )}
                          </div>
                          <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                            {a.description}
                          </p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {filtered.length === 0 && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No actions match &ldquo;{query}&rdquo;.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
