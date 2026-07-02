"use client";

import { useEffect, useRef } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Upload,
  UploadCloud,
  X,
} from "lucide-react";

import {
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogDescription,
  ResponsiveDialogFooter,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
} from "@/components/ui/responsive-dialog";
import { Button } from "@/components/ui/button";
import { FileIcon } from "./file-icon";
import { cn } from "@/lib/utils";
import type { DocumentFileType } from "@/lib/types";
import {
  extOf,
  MAX_MB,
  useUploads,
  type UploadItem,
} from "./upload-context";

const ACCEPTED_TYPES = ".pdf,.docx,.txt,.md,.xlsx,.pptx,.html,.htm,.csv";

const FILE_ICON_TYPES: Record<string, DocumentFileType> = {
  pdf: "pdf",
  docx: "docx",
  md: "md",
  xlsx: "xlsx",
  pptx: "pptx",
  html: "html",
  htm: "html",
  csv: "csv",
};

function asFileIconType(name: string): DocumentFileType {
  return FILE_ICON_TYPES[extOf(name)] ?? "txt";
}

/**
 * Trigger button. Lives on pages that should expose "Upload documents" (the
 * documents index). The dialog itself is mounted globally as
 * <UploadDialogHost /> so the floating widget can re-open it from any route.
 */
export function UploadDialog() {
  const { setDialogOpen } = useUploads();
  return (
    <Button onClick={() => setDialogOpen(true)}>
      <Upload />
      Upload documents
    </Button>
  );
}

/**
 * The dialog body. Mounted once in the dashboard layout so the upload
 * surface is available from any route — closing the dialog never cancels
 * the in-flight queue (the floating <UploadWidget /> takes over). All state
 * comes from `useUploads()`.
 */
export function UploadDialogHost() {
  const {
    items,
    isRunning,
    dialogOpen,
    summary,
    enqueue,
    startAll,
    cancel,
    cancelAll,
    remove,
    clearCompleted,
    setDialogOpen,
  } = useUploads();
  const inputRef = useRef<HTMLInputElement>(null);

  const hasQueue = items.length > 0;
  const allDone =
    hasQueue && summary.pending === 0 && summary.inFlight === 0;
  const allSucceeded = allDone && summary.failed === 0;
  const startLabel =
    summary.failed > 0 && !isRunning ? "Retry failed" : "Start upload";

  // Auto-close on a clean run, then drop the success rows so re-opening the
  // dialog shows a fresh empty state. Failed runs stay open so the user can
  // inspect or retry.
  useEffect(() => {
    if (dialogOpen && allSucceeded && !isRunning) {
      const t = setTimeout(() => {
        setDialogOpen(false);
        clearCompleted();
      }, 1100);
      return () => clearTimeout(t);
    }
  }, [dialogOpen, allSucceeded, isRunning, setDialogOpen, clearCompleted]);

  function handleOpenChange(next: boolean) {
    setDialogOpen(next);
    // Purge stale success/error rows on close, but only when nothing is
    // queued for a future run. In-flight uploads continue via the provider.
    if (
      !next &&
      !isRunning &&
      summary.pending === 0 &&
      summary.inFlight === 0
    ) {
      clearCompleted();
    }
  }

  return (
    // ResponsiveDialog: centered Radix Dialog on desktop, vaul bottom sheet
    // on mobile. Body content is wrapped in a `contents` shim on desktop so
    // the Dialog's grid + gap-4 still applies; on mobile it becomes an
    // explicit flex column with horizontal padding (the drawer primitive
    // intentionally leaves body padding to callers so headers/footers can
    // bleed differently).
    <ResponsiveDialog open={dialogOpen} onOpenChange={handleOpenChange}>
      <ResponsiveDialogContent>
        <ResponsiveDialogHeader>
          <ResponsiveDialogTitle>Upload Documents</ResponsiveDialogTitle>
          <ResponsiveDialogDescription>
            You can close this dialog — uploads keep running.
          </ResponsiveDialogDescription>
        </ResponsiveDialogHeader>

        <div className="flex flex-col gap-4 px-4 pb-4 md:contents">
          <Dropzone hasQueue={hasQueue} onPick={enqueue} inputRef={inputRef} />
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_TYPES}
            onChange={(e) => {
              if (e.target.files) enqueue(e.target.files);
              // Allow re-selecting the same file after a failure.
              if (inputRef.current) inputRef.current.value = "";
            }}
            className="hidden"
          />

          {hasQueue && (
            <div className="max-h-64 overflow-y-auto rounded-xl border border-border">
              <ul className="divide-y divide-border">
                {items.map((item) => (
                  <QueueRow
                    key={item.key}
                    item={item}
                    onRemove={() => remove(item.key)}
                    onCancel={() => cancel(item.key)}
                  />
                ))}
              </ul>
            </div>
          )}

          {hasQueue && (
            <p
              className={cn(
                "text-xs",
                summary.failed > 0
                  ? "text-destructive"
                  : "text-muted-foreground",
              )}
            >
              {isRunning
                ? `Uploading ${summary.done + summary.inFlight} of ${summary.total}…`
                : allDone
                  ? summary.failed > 0
                    ? `${summary.done} uploaded, ${summary.failed} failed.`
                    : `All ${summary.done} uploaded.`
                  : `${summary.total} file${summary.total === 1 ? "" : "s"} ready.`}
            </p>
          )}
        </div>

        <ResponsiveDialogFooter>
          {isRunning ? (
            <Button variant="outline" onClick={cancelAll}>
              Cancel all
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={() => handleOpenChange(false)}
            >
              {allDone ? "Close" : hasQueue ? "Cancel" : "Close"}
            </Button>
          )}
          <Button
            onClick={startAll}
            disabled={
              isRunning ||
              !hasQueue ||
              summary.pending + summary.failed === 0
            }
          >
            {isRunning && <Loader2 className="animate-spin" />}
            {isRunning
              ? `Uploading ${summary.done + summary.inFlight}/${summary.total}…`
              : startLabel}
          </Button>
        </ResponsiveDialogFooter>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}

function Dropzone({
  hasQueue,
  onPick,
  inputRef,
}: {
  hasQueue: boolean;
  onPick: (files: FileList | File[]) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const dragRef = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={dragRef}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        dragRef.current?.classList.add("border-brand", "bg-brand-tint");
      }}
      onDragLeave={() => {
        dragRef.current?.classList.remove("border-brand", "bg-brand-tint");
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragRef.current?.classList.remove("border-brand", "bg-brand-tint");
        if (e.dataTransfer.files.length > 0) onPick(e.dataTransfer.files);
      }}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
        hasQueue
          ? "border-input"
          : "border-input hover:border-brand/60 hover:bg-brand-tint/40",
      )}
    >
      <UploadCloud className="mb-2 h-6 w-6 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">
        {hasQueue ? "Add more files" : "Drag files or click to select"}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">
          Up to {MAX_MB} MB each
      </p>
    </div>
  );
}

function QueueRow({
  item,
  onRemove,
  onCancel,
}: {
  item: UploadItem;
  onRemove: () => void;
  onCancel: () => void;
}) {
  const inFlight =
    item.status === "preparing" ||
    item.status === "uploading" ||
    item.status === "completing";
  const statusLabel =
    item.status === "pending"
      ? "Waiting"
      : item.status === "preparing"
        ? "Preparing"
        : item.status === "uploading"
          ? `${item.progress}%`
          : item.status === "completing"
            ? "Finishing"
            : item.status === "success"
              ? "Uploaded"
              : "Failed";

  return (
    <li className="flex items-center gap-3 px-3 py-2">
      <FileIcon
        type={asFileIconType(item.file.name)}
        className="h-4 w-4 shrink-0 text-muted-foreground"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">
          {item.file.name}
        </p>
        <div className="mt-1 flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
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
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {statusLabel}
          </span>
        </div>
        {item.status === "error" && item.error && (
          <p className="mt-1 truncate text-[11px] text-destructive">
            {item.error}
          </p>
        )}
      </div>
      {item.status === "success" ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
      ) : item.status === "error" ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${item.file.name}`}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <AlertTriangle className="h-4 w-4 text-destructive" />
        </button>
      ) : inFlight ? (
        <button
          type="button"
          onClick={onCancel}
          aria-label={`Cancel ${item.file.name}`}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      ) : (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${item.file.name}`}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </li>
  );
}
