"use client";

import { useMemo, useState } from "react";
import { Search, Sparkles } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  AUTOFLOW_TEMPLATES,
  TEMPLATE_CATEGORIES,
  type AutoflowTemplate,
} from "@/lib/autoflow/templates";
import { getIcon } from "@/lib/autoflow/icons";
import { getTrigger } from "@/lib/autoflow/triggers";
import { getAction } from "@/lib/autoflow/catalog";

interface TemplatesGalleryProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onPick: (template: AutoflowTemplate) => void;
}

export function TemplatesGallery({ open, onOpenChange, onPick }: TemplatesGalleryProps) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<AutoflowTemplate["category"] | "all">("all");

  const filtered = useMemo(() => {
    return AUTOFLOW_TEMPLATES.filter((t) => {
      if (category !== "all" && t.category !== category) return false;
      if (!query) return true;
      const q = query.toLowerCase();
      return (
        t.title.toLowerCase().includes(q) ||
        t.tagline.toLowerCase().includes(q) ||
        t.draft.actions.some((a) => a.type.toLowerCase().includes(q))
      );
    });
  }, [category, query]);

  const handlePick = (t: AutoflowTemplate) => {
    onPick(t);
    onOpenChange(false);
    setQuery("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] max-w-3xl overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-5 text-primary" />
            Start from a template
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
            <Input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search templates…"
              className="pl-9"
            />
          </div>

          <div className="flex flex-wrap gap-1.5">
            <CategoryPill
              active={category === "all"}
              onClick={() => setCategory("all")}
              label={`All · ${AUTOFLOW_TEMPLATES.length}`}
            />
            {TEMPLATE_CATEGORIES.map((c) => {
              const n = AUTOFLOW_TEMPLATES.filter((t) => t.category === c.id).length;
              if (n === 0) return null;
              return (
                <CategoryPill
                  key={c.id}
                  active={category === c.id}
                  onClick={() => setCategory(c.id)}
                  label={`${c.label} · ${n}`}
                />
              );
            })}
          </div>

          <div className="grid max-h-[60vh] gap-3 overflow-y-auto pr-1 sm:grid-cols-2">
            {filtered.length === 0 ? (
              <p className="col-span-full py-10 text-center text-sm text-muted-foreground">
                Nothing matches &ldquo;{query}&rdquo;.
              </p>
            ) : (
              filtered.map((t) => (
                <TemplateCard key={t.id} template={t} onPick={() => handlePick(t)} />
              ))
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function CategoryPill({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1 text-xs transition-colors",
        active ? "border-primary bg-primary text-primary-foreground" : "hover:border-foreground/30",
      )}
    >
      {label}
    </button>
  );
}

function TemplateCard({
  template,
  onPick,
}: {
  template: AutoflowTemplate;
  onPick: () => void;
}) {
  const Icon = getIcon(template.icon);
  const trigger = getTrigger(template.draft.trigger_type);
  const actions = template.draft.actions
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((a) => getAction(a.type));

  return (
    <button
      type="button"
      onClick={onPick}
      className="group flex flex-col items-start gap-3 rounded-lg border bg-card p-4 text-left transition-all hover:border-primary hover:shadow-md"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium leading-tight">{template.title}</p>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{template.tagline}</p>
        </div>
      </div>

      <div className="flex w-full flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        <Badge variant="outline" className="text-[10px]">{trigger.label}</Badge>
        <span>→</span>
        {actions.map((a, i) => (
          <Badge key={i} variant="outline" className="text-[10px]">
            {a.shortLabel}
          </Badge>
        ))}
      </div>
    </button>
  );
}
