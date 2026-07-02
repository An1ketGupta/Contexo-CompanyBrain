"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Maximize2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { FileIcon } from "./file-icon";
import { cn } from "@/lib/utils";
import type { DocumentFileType } from "@/lib/types";
import {
  extOf,
  useUploads,
  type UploadItem,
  type UploadStatus,
} from "./upload-context";

const AUTO_DISMISS_MS = 4000;

function asFileIconType(name: string): DocumentFileType {
  const ext = extOf(name);
  return ext === "pdf" || ext === "docx" || ext === "md"
    ? (ext as DocumentFileType)
    : "txt";
}

export function UploadWidget() {
  const {
    items,
    isRunning,
    dialogOpen,
    summary,
    cancel,
    cancelAll,
    clearCompleted,
    setDialogOpen,
  } = useUploads();
  const [collapsed, setCollapsed] = useState(false);

  // Auto-dismiss the widget once every item in the queue has finished
  // successfully — matches the dialog's auto-close behavior so a clean run
  // leaves no UI residue. If any item failed we leave the widget visible so
  // the user can retry from the dialog.
  useEffect(() => {
    if (
      !isRunning &&
      items.length > 0 &&
      summary.failed === 0 &&
      summary.pending === 0 &&
      summary.inFlight === 0 &&
      summary.done === items.length
    ) {
      const t = setTimeout(() => clearCompleted(), AUTO_DISMISS_MS);
      return () => clearTimeout(t);
    }
  }, [
    isRunning,
    items.length,
    summary.failed,
    summary.pending,
    summary.inFlight,
    summary.done,
    clearCompleted,
  ]);

  const overallProgress = useMemo(() => {
    if (items.length === 0) return 0;
    const total = items.reduce((acc, it) => {
      if (it.status === "success") return acc + 100;
      if (it.status === "error") return acc + 0;
      return acc + it.progress;
    }, 0);
    return Math.round(total / items.length);
  }, [items]);

  // Don't render while the dialog is the active surface, or when there's
  // nothing to show. Dialog has its own queue view; double-stacking would be
  // visual noise.
  if (dialogOpen || items.length === 0) return null;

  const headline = isRunning
    ? `Uploading ${summary.done + summary.inFlight} of ${summary.total}`
    : summary.failed > 0
      ? `${summary.failed} failed · ${summary.done}/${summary.total} done`
      : summary.done === summary.total
        ? `All ${summary.total} uploaded`
        : `${summary.pending} ready`;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-40 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-border bg-background shadow-lg"
    >
      <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-3 py-2">
        {isRunning ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
        ) : summary.failed > 0 ? (
          <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
        ) : (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
        )}
        <p className="min-w-0 flex-1 truncate text-sm font-medium">{headline}</p>
        <button
          type="button"
          aria-label={collapsed ? "Expand" : "Collapse"}
          onClick={() => setCollapsed((v) => !v)}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          {collapsed ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          type="button"
          aria-label="Open upload dialog"
          onClick={() => setDialogOpen(true)}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
        {!isRunning && (
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => clearCompleted()}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {!collapsed && (
        <>
          <div className="px-3 pt-2">
            <div className="h-1 overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  summary.failed > 0 && !isRunning
                    ? "bg-destructive"
                    : !isRunning && summary.done === summary.total
                      ? "bg-success"
                      : "bg-primary",
                )}
                style={{ width: `${overallProgress}%` }}
              />
            </div>
          </div>

          <ul className="max-h-56 divide-y divide-border overflow-y-auto px-1 py-1">
            {items.map((item) => (
              <WidgetRow key={item.key} item={item} onCancel={cancel} />
            ))}
          </ul>

          {isRunning && (
            <div className="border-t border-border px-3 py-2">
              <Button
                variant="outline"
                size="sm"
                onClick={cancelAll}
                className="w-full"
              >
                Cancel all
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function WidgetRow({
  item,
  onCancel,
}: {
  item: UploadItem;
  onCancel: (key: string) => void;
}) {
  const inFlight = isInFlight(item.status);
  return (
    <li className="flex items-start gap-2 px-2 py-2">
      <FileIcon
        type={asFileIconType(item.file.name)}
        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium text-foreground">
          {item.file.name}
        </p>
        <div className="mt-1 flex items-center gap-2">
          <div className="h-0.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-all",
                item.status === "error"
                  ? "bg-destructive"
                  : item.status === "success"
                    ? "bg-success"
                    : "bg-primary",
              )}
              style={{ width: `${item.progress}%` }}
            />
          </div>
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
            {statusLabel(item)}
          </span>
        </div>
        {item.status === "error" && item.error && (
          <p className="mt-0.5 truncate text-[10px] text-destructive">
            {item.error}
          </p>
        )}
      </div>
      {item.status === "success" ? (
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
      ) : item.status === "error" ? (
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
      ) : inFlight ? (
        <button
          type="button"
          onClick={() => onCancel(item.key)}
          aria-label={`Cancel ${item.file.name}`}
          className="mt-0.5 rounded-md p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="h-3 w-3" />
        </button>
      ) : null}
    </li>
  );
}

function isInFlight(status: UploadStatus): boolean {
  return (
    status === "preparing" ||
    status === "uploading" ||
    status === "completing" ||
    status === "pending"
  );
}

function statusLabel(item: UploadItem): string {
  switch (item.status) {
    case "pending":
      return "Waiting";
    case "preparing":
      return "Preparing";
    case "uploading":
      return `${item.progress}%`;
    case "completing":
      return "Finishing";
    case "success":
      return "Done";
    case "error":
      return "Failed";
  }
}
