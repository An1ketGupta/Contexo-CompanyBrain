"use client";

import { Tag, X } from "lucide-react";
import { useDocumentTags } from "@/hooks/use-documents";
import { cn } from "@/lib/utils";

interface Props {
  value: string[];
  onChange: (next: string[]) => void;
  /** Cap the chip row visually — extras still selectable via the … button. */
  visible?: number;
}

/**
 * Tag-chip scope selector shown on the new-chat surface.
 *
 * Renders nothing if the org has no tags — the affordance only earns its
 * pixels when there's something to scope to. Selection becomes the
 * conversation's scope on the first send (locked at creation, mirrors
 * doc-scope semantics).
 */
export function ChatScopeSelector({ value, onChange, visible = 8 }: Props) {
  const { tags } = useDocumentTags();
  if (!tags.length) return null;

  const toggle = (tag: string) => {
    if (value.includes(tag)) {
      onChange(value.filter((t) => t !== tag));
    } else {
      onChange([...value, tag]);
    }
  };

  return (
    <div className="mx-4 mt-3 flex flex-wrap items-center gap-1.5 rounded-lg border border-dashed border-border bg-muted/30 px-3 py-2 md:mx-6">
      <span className="mr-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Tag className="h-3 w-3" />
        Search in:
      </span>
      {tags.slice(0, visible).map((t) => {
        const active = value.includes(t.tag);
        return (
          <button
            key={t.tag}
            type="button"
            onClick={() => toggle(t.tag)}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
              active
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground",
            )}
            title={`${t.count} document${t.count === 1 ? "" : "s"}`}
          >
            {t.tag}
            <span
              className={cn(
                "text-[10px] tabular-nums opacity-70",
                active ? "opacity-90" : "",
              )}
            >
              {t.count}
            </span>
          </button>
        );
      })}
      {value.length > 0 && (
        <button
          type="button"
          onClick={() => onChange([])}
          className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <X className="h-3 w-3" />
          Clear
        </button>
      )}
    </div>
  );
}
