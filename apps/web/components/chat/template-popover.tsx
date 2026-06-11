"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { LayoutTemplate, Plus, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { useTemplates } from "@/hooks/use-templates";
import { cn } from "@/lib/utils";
import type { PromptTemplate, TemplateCategory } from "@/lib/types";
import { CreateTemplateDialog } from "./create-template-dialog";
import { UseTemplateDialog } from "./use-template-dialog";

const CATEGORIES: Array<TemplateCategory | "All"> = [
  "All",
  "Email",
  "Job Description",
  "Announcement",
  "Policy Q&A",
  "Meeting Prep",
  "Customer Response",
  "Slack Reply",
  "Other",
];

interface Props {
  onSelect: (text: string) => void;
  /** Optional render slot — defaults to a small icon button. */
  trigger?: ReactNode;
}

/**
 * Template library opener — icon button in the chat input row.
 *
 * Panel renders below-left so it doesn't clip the chat input. Outside-click
 * + Escape close it. We don't use Radix Popover because the project's UI
 * primitive set doesn't ship one and the affordance here is simple enough
 * that a hand-rolled panel is shorter than wiring a new dep.
 */
export function TemplatePopover({ onSelect, trigger }: Props) {
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [varsDialogOpen, setVarsDialogOpen] = useState(false);
  const [varsTemplate, setVarsTemplate] = useState<PromptTemplate | null>(null);
  const [activeCategory, setActiveCategory] = useState<TemplateCategory | "All">(
    "All",
  );
  const [search, setSearch] = useState("");
  const panelRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const { templates, loading, recordUse } = useTemplates(activeCategory, search);

  // Outside-click + escape to close.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const node = panelRef.current;
      const trig = triggerRef.current;
      if (!node || node.contains(e.target as Node)) return;
      if (trig && trig.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = useCallback(
    (template: PromptTemplate) => {
      // V4 #70 — if the template has {{variables}}, route through the use
      // dialog instead of dumping the raw placeholder text into the composer.
      const hasVariables = (template.variables ?? []).length > 0;
      if (hasVariables) {
        setVarsTemplate(template);
        setVarsDialogOpen(true);
        setOpen(false);
        return;
      }
      onSelect(template.template_text);
      void recordUse(template.id);
      setOpen(false);
    },
    [onSelect, recordUse],
  );

  const handleVarsResolved = useCallback(
    (text: string) => {
      onSelect(text);
      if (varsTemplate) void recordUse(varsTemplate.id);
      setVarsTemplate(null);
    },
    [onSelect, recordUse, varsTemplate],
  );

  const filtered = useMemo(() => templates, [templates]);

  return (
    <div className="relative">
      {trigger ? (
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label="Templates"
          title="Templates"
        >
          {trigger}
        </button>
      ) : (
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label="Templates"
          title="Insert a template"
        >
          <LayoutTemplate className="h-4 w-4" />
        </button>
      )}

      {open && (
        <div
          ref={panelRef}
          className="absolute bottom-full left-0 z-30 mb-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border bg-popover shadow-lg"
        >
          <div className="border-b border-border p-3">
            <p className="mb-2 text-sm font-semibold">Templates</p>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search templates…"
                className="h-8 pl-7 text-sm"
              />
            </div>
          </div>

          <div className="flex gap-1 overflow-x-auto border-b border-border p-2 [scrollbar-width:thin]">
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                type="button"
                onClick={() => setActiveCategory(cat)}
                className={cn(
                  "whitespace-nowrap rounded-md px-2.5 py-1 text-xs transition-colors",
                  activeCategory === cat
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                {cat}
              </button>
            ))}
          </div>

          <div className="max-h-72 overflow-y-auto">
            {loading ? (
              <p className="p-6 text-center text-xs text-muted-foreground">
                Loading…
              </p>
            ) : filtered.length === 0 ? (
              <p className="p-6 text-center text-xs text-muted-foreground">
                No templates found.
              </p>
            ) : (
              filtered.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => pick(t)}
                  className="block w-full border-b border-border/60 px-3 py-2 text-left transition-colors last:border-0 hover:bg-muted/60"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-medium text-foreground">
                      {t.title}
                    </p>
                    <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {t.category}
                    </span>
                  </div>
                  {t.description && (
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {t.description}
                    </p>
                  )}
                </button>
              ))
            )}
          </div>

          <div className="border-t border-border p-2">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setCreateOpen(true);
              }}
              className="flex w-full items-center justify-center gap-1 rounded-md py-1.5 text-xs font-medium text-primary hover:bg-primary/10"
            >
              <Plus className="h-3.5 w-3.5" />
              Create new template
            </button>
          </div>
        </div>
      )}

      <CreateTemplateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
      />

      <UseTemplateDialog
        template={varsTemplate}
        open={varsDialogOpen}
        onOpenChange={(v) => {
          setVarsDialogOpen(v);
          if (!v) setVarsTemplate(null);
        }}
        onResolved={handleVarsResolved}
      />
    </div>
  );
}
