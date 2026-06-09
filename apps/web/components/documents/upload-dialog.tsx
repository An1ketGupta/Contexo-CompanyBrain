"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Upload,
  UploadCloud,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { FileIcon } from "./file-icon";
import { cn } from "@/lib/utils";
import type { DocumentFileType } from "@/lib/types";

const ACCEPTED_TYPES = ".pdf,.docx,.txt,.md";
const ACCEPTED_EXT = new Set(["pdf", "docx", "txt", "md"]);
const MAX_MB = 50;
const MAX_BYTES = MAX_MB * 1024 * 1024;

interface UploadDialogProps {
  onUploadComplete?: () => void;
}

type QueueStatus =
  | "pending"
  | "preparing"
  | "uploading"
  | "completing"
  | "success"
  | "error";

interface QueueItem {
  /** Local-only id; the server doc_id is captured separately once minted. */
  key: string;
  file: File;
  status: QueueStatus;
  progress: number;
  error?: string;
}

function extOf(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

/** Narrow a free-form extension to the icon's expected type. */
function asFileIconType(name: string): DocumentFileType {
  const ext = extOf(name);
  return ext === "pdf" || ext === "docx" || ext === "md"
    ? (ext as DocumentFileType)
    : "txt";
}

function makeKey(file: File): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

function uploadWithProgress(
  url: string,
  file: File,
  onProgress: (pct: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Storage upload failed (${xhr.status})`));
    });
    xhr.addEventListener("error", () =>
      reject(new Error("Network error during upload")),
    );
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));

    signal?.addEventListener("abort", () => xhr.abort());

    xhr.open("PUT", url);
    xhr.setRequestHeader(
      "content-type",
      file.type || "application/octet-stream",
    );
    xhr.send(file);
  });
}

export function UploadDialog({ onUploadComplete }: UploadDialogProps) {
  const [open, setOpen] = useState(false);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [running, setRunning] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const summary = useMemo(() => {
    const total = queue.length;
    const done = queue.filter((q) => q.status === "success").length;
    const failed = queue.filter((q) => q.status === "error").length;
    const pending = queue.filter(
      (q) => q.status === "pending" || q.status === "preparing" || q.status === "uploading" || q.status === "completing",
    ).length;
    return { total, done, failed, pending };
  }, [queue]);

  const updateItem = (key: string, patch: Partial<QueueItem>) =>
    setQueue((items) =>
      items.map((it) => (it.key === key ? { ...it, ...patch } : it)),
    );

  function handleOpenChange(next: boolean) {
    if (running) return;
    setOpen(next);
    if (!next) reset();
  }

  function reset() {
    setQueue([]);
    setDragOver(false);
    setRunning(false);
    abortRef.current = null;
  }

  function pickFiles(picked: FileList | File[] | null) {
    if (!picked) return;
    const incoming = Array.from(picked);
    if (incoming.length === 0) return;

    const accepted: QueueItem[] = [];
    let rejected = 0;
    for (const file of incoming) {
      if (file.size > MAX_BYTES) {
        toast.error(`"${file.name}" is over ${MAX_MB} MB — skipped.`);
        rejected++;
        continue;
      }
      if (!ACCEPTED_EXT.has(extOf(file.name))) {
        toast.error(`"${file.name}" isn't a supported type — skipped.`);
        rejected++;
        continue;
      }
      accepted.push({
        key: makeKey(file),
        file,
        status: "pending",
        progress: 0,
      });
    }

    if (accepted.length === 0 && rejected === 0) return;
    // Dedupe against existing queue entries by name+size+mtime.
    setQueue((items) => {
      const seen = new Set(items.map((it) => it.key));
      const fresh = accepted.filter((it) => !seen.has(it.key));
      return [...items, ...fresh];
    });
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (running) return;
    pickFiles(e.dataTransfer.files);
  }

  function removeFromQueue(key: string) {
    if (running) return;
    setQueue((items) => items.filter((it) => it.key !== key));
  }

  async function processOne(item: QueueItem, signal: AbortSignal): Promise<void> {
    updateItem(item.key, { status: "preparing", progress: 0, error: undefined });

    const initRes = await fetch("/api/documents/upload", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        filename: item.file.name,
        content_type: item.file.type || "application/octet-stream",
        file_size: item.file.size,
      }),
      signal,
    });
    if (!initRes.ok) {
      const body = await initRes.json().catch(() => ({}));
      throw new Error(body.detail ?? `Upload init failed (${initRes.status})`);
    }
    const { doc_id, upload_url } = (await initRes.json()) as {
      doc_id: string;
      upload_url: string;
    };

    updateItem(item.key, { status: "uploading", progress: 0 });
    await uploadWithProgress(
      upload_url,
      item.file,
      (pct) => updateItem(item.key, { progress: pct }),
      signal,
    );

    updateItem(item.key, { status: "completing", progress: 100 });
    const completeRes = await fetch("/api/documents/upload/complete", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_id }),
      signal,
    });
    if (!completeRes.ok) {
      const body = await completeRes.json().catch(() => ({}));
      throw new Error(
        body.detail ?? `Failed to start processing (${completeRes.status})`,
      );
    }

    updateItem(item.key, { status: "success", progress: 100 });
  }

  async function startQueue() {
    const toProcess = queue.filter(
      (q) => q.status === "pending" || q.status === "error",
    );
    if (toProcess.length === 0) return;

    setRunning(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let anySuccess = false;
    for (const item of toProcess) {
      if (ctrl.signal.aborted) break;
      try {
        await processOne(item, ctrl.signal);
        anySuccess = true;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Upload failed.";
        updateItem(item.key, { status: "error", error: message });
      }
    }

    abortRef.current = null;
    setRunning(false);

    if (anySuccess) {
      onUploadComplete?.();
      // Functional read so the toast reflects the just-finished run, not a
      // stale snapshot from the start of the function.
      setQueue((latest) => {
        const ok = latest.filter((q) => q.status === "success").length;
        const failed = latest.filter((q) => q.status === "error").length;
        if (ok > 0 && failed === 0) {
          toast.success(
            ok === 1
              ? `Uploaded 1 document. Processing started.`
              : `Uploaded ${ok} documents. Processing started.`,
          );
        } else if (ok > 0 && failed > 0) {
          toast.message(
            `Uploaded ${ok} of ${ok + failed}. ${failed} failed — click Retry.`,
          );
        }
        return latest;
      });
    }
  }

  function cancelAll() {
    abortRef.current?.abort();
  }

  // Auto-close shortly after a fully successful run.
  useEffect(() => {
    if (!running && queue.length > 0 && summary.failed === 0 && summary.done === queue.length) {
      const t = setTimeout(() => {
        setOpen(false);
        reset();
      }, 1100);
      return () => clearTimeout(t);
    }
  }, [running, queue.length, summary.failed, summary.done]);

  const hasQueue = queue.length > 0;
  const allDone = hasQueue && summary.pending === 0;
  const startLabel = summary.failed > 0 && !running ? "Retry failed" : "Start upload";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <Upload />
          Upload documents
        </Button>
      </DialogTrigger>
      <DialogContent showClose={!running}>
        <DialogHeader>
          <DialogTitle>Upload documents</DialogTitle>
          <DialogDescription>
            Drop one or more files. PDF, DOCX, TXT, or MD up to {MAX_MB} MB each.
            Files upload one at a time.
          </DialogDescription>
        </DialogHeader>

        <div
          onClick={() => !running && inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            if (!running) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-8 text-center transition-colors",
            hasQueue
              ? "border-input"
              : dragOver
                ? "border-primary bg-primary/10"
                : "border-input hover:border-primary/60 hover:bg-muted/40",
            running && "pointer-events-none opacity-60",
          )}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_TYPES}
            onChange={(e) => {
              pickFiles(e.target.files);
              // Allow re-selecting the same file after a failure.
              if (inputRef.current) inputRef.current.value = "";
            }}
            className="hidden"
          />
          <UploadCloud className="mb-2 h-6 w-6 text-muted-foreground" />
          <p className="text-sm font-medium text-foreground">
            {hasQueue ? "Add more files" : "Drag files or click to select"}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            PDF, DOCX, TXT, MD — up to {MAX_MB} MB each
          </p>
        </div>

        {hasQueue && (
          <div className="max-h-64 overflow-y-auto rounded-md border border-border">
            <ul className="divide-y divide-border">
              {queue.map((item) => (
                <QueueRow
                  key={item.key}
                  item={item}
                  disabled={running}
                  onRemove={() => removeFromQueue(item.key)}
                />
              ))}
            </ul>
          </div>
        )}

        {hasQueue && (
          <p
            className={cn(
              "text-xs",
              summary.failed > 0 ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {running
              ? `Uploading ${summary.done + 1} of ${summary.total}…`
              : allDone
                ? summary.failed > 0
                  ? `${summary.done} uploaded, ${summary.failed} failed.`
                  : `All ${summary.done} uploaded.`
                : `${summary.total} file${summary.total === 1 ? "" : "s"} ready.`}
          </p>
        )}

        <DialogFooter>
          {running ? (
            <Button variant="outline" onClick={cancelAll}>
              Cancel
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={running}
            >
              {allDone ? "Close" : "Cancel"}
            </Button>
          )}
          <Button
            onClick={startQueue}
            disabled={running || !hasQueue || summary.pending + summary.failed === 0}
          >
            {running && <Loader2 className="animate-spin" />}
            {running
              ? `Uploading ${summary.done + 1}/${summary.total}…`
              : startLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function QueueRow({
  item,
  disabled,
  onRemove,
}: {
  item: QueueItem;
  disabled: boolean;
  onRemove: () => void;
}) {
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
                    ? "bg-emerald-500"
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
        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
      ) : item.status === "error" ? (
        <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
      ) : (
        <button
          type="button"
          onClick={onRemove}
          disabled={disabled}
          aria-label={`Remove ${item.file.name}`}
          className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </li>
  );
}
