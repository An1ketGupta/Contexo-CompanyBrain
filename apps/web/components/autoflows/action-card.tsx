"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getAction } from "@/lib/autoflow/catalog";
import { getIcon } from "@/lib/autoflow/icons";
import type { ActionStep } from "@/lib/autoflow/types";

interface ActionCardProps {
  step: ActionStep;
  index: number;
  selected: boolean;
  invalid?: boolean;
  onClick: () => void;
  onDelete: () => void;
}

const CATEGORY_STYLES = {
  ai: "bg-violet-tint text-violet",
  notify: "bg-brand-tint text-brand",
  integrations: "bg-success-tint text-success-ink",
  control: "bg-amber-tint text-amber-ink",
};

export function ActionCard({ step, index, selected, invalid, onClick, onDelete }: ActionCardProps) {
  const entry = getAction(step.type);
  const Icon = getIcon(entry.icon);
  const summary = buildSummary(step, entry);

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: step.id,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "group relative rounded-2xl border border-border bg-card shadow-sm transition-all hover:shadow-md",
        selected && "border-brand ring-2 ring-brand/25",
        invalid && !selected && "border-destructive/40",
        isDragging && "z-10 opacity-50 shadow-xl",
      )}
    >
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-start gap-3 p-4 text-left focus:outline-none"
      >
        <button
          type="button"
          aria-label="Drag to reorder"
          {...attributes}
          {...listeners}
          onClick={(e) => e.stopPropagation()}
          className="mt-1 -ml-1 cursor-grab touch-none rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted active:cursor-grabbing group-hover:opacity-100"
        >
          <GripVertical className="size-4" />
        </button>

        <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", CATEGORY_STYLES[entry.category])}>
          <Icon className="size-5" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
              Step {index + 1}
            </span>
            <span className="text-xs text-muted-foreground">{entry.shortLabel}</span>
            {!entry.available && <Badge variant="destructive" className="text-[10px]">Unavailable</Badge>}
            {invalid && <Badge variant="destructive" className="text-[10px]">Needs setup</Badge>}
          </div>
          <p className="mt-2 text-sm font-semibold">{summary.headline}</p>
          {summary.detail && (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{summary.detail}</p>
          )}
        </div>

        <Button
          variant="ghost"
          size="sm"
          aria-label="Delete step"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="opacity-0 transition-opacity group-hover:opacity-100"
        >
          <Trash2 className="size-4" />
        </Button>
      </button>
    </div>
  );
}

function buildSummary(step: ActionStep, entry: ReturnType<typeof getAction>): { headline: string; detail: string | null } {
  const cfg = step.config;
  switch (step.type) {
    case "generate_output": {
      const prompt = String(cfg.prompt ?? "");
      return { headline: prompt ? truncate(prompt, 80) : entry.label, detail: null };
    }
    case "send_email": {
      const to = String(cfg.to ?? "");
      const subject = String(cfg.subject ?? "");
      return {
        headline: subject ? `"${truncate(subject, 60)}"` : "Send email",
        detail: to ? `→ ${to}` : null,
      };
    }
    case "post_slack": {
      const channel = String(cfg.channel_id ?? "");
      const text = String(cfg.text ?? "");
      return {
        headline: text ? truncate(text, 60) : "Post to Slack",
        detail: channel ? `#${channel}` : null,
      };
    }
    case "create_notion_page": {
      const title = String(cfg.title ?? "");
      return { headline: title ? truncate(title, 60) : "Create Notion page", detail: null };
    }
    case "notify_admin": {
      const title = String(cfg.title ?? "");
      return { headline: title ? truncate(title, 60) : "Notify admins", detail: null };
    }
    case "emit_webhook": {
      const event = String(cfg.event ?? "");
      return { headline: event || "Emit webhook", detail: null };
    }
    case "hold_for_approval": {
      return { headline: "Hold for approval", detail: String(cfg.note ?? "") || null };
    }
    case "create_task":
      return { headline: "Create task — unavailable", detail: null };
    default:
      return { headline: entry.label, detail: null };
  }
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1)}…`;
}
