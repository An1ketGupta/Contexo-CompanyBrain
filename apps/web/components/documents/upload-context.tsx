"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

export type UploadStatus =
  | "pending"
  | "preparing"
  | "uploading"
  | "completing"
  | "success"
  | "error";

export interface UploadItem {
  /** Local-only id used to track the item in the queue. */
  key: string;
  file: File;
  status: UploadStatus;
  progress: number;
  error?: string;
  /** Server-minted doc row id, set once /api/documents/upload responds. */
  doc_id?: string;
}

export interface UploadSummary {
  total: number;
  done: number;
  failed: number;
  pending: number;
  inFlight: number;
}

interface UploadContextValue {
  items: UploadItem[];
  isRunning: boolean;
  dialogOpen: boolean;
  summary: UploadSummary;
  enqueue: (files: File[] | FileList) => { added: number; rejected: number };
  startAll: () => void;
  cancel: (key: string) => void;
  cancelAll: () => void;
  remove: (key: string) => void;
  clearCompleted: () => void;
  setDialogOpen: (open: boolean) => void;
}

const UploadContext = createContext<UploadContextValue | null>(null);

export const ACCEPTED_EXT = new Set([
  "pdf",
  "docx",
  "txt",
  "md",
  "xlsx",
  "pptx",
  "html",
  "csv",
]);
export const MAX_MB = 50;
export const MAX_BYTES = MAX_MB * 1024 * 1024;
const REFRESH_EVENT = "documents:refresh";

export function extOf(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

function makeKey(file: File): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

function uploadWithProgress(
  url: string,
  file: File,
  onProgress: (pct: number) => void,
  signal: AbortSignal,
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

    signal.addEventListener("abort", () => xhr.abort());

    xhr.open("PUT", url);
    xhr.setRequestHeader(
      "content-type",
      file.type || "application/octet-stream",
    );
    xhr.send(file);
  });
}

async function processOne(
  item: UploadItem,
  signal: AbortSignal,
  updateItem: (key: string, patch: Partial<UploadItem>) => void,
): Promise<void> {
  updateItem(item.key, { status: "preparing", progress: 0, error: undefined });

  let doc_id: string;
  let upload_url: string;

  if (item.doc_id) {
    // Retry path — reuse the existing failed/pending DB row instead of minting
    // a duplicate. Backend resets status to "pending" and hands back a fresh
    // signed URL for the same storage path.
    const refreshRes = await fetch("/api/documents/upload/refresh", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ doc_id: item.doc_id }),
      signal,
    });
    if (!refreshRes.ok) {
      // If the prior row is gone (404) or no longer retryable (409, e.g. it
      // somehow finished), fall back to a fresh init so the user still
      // recovers — drop the stale doc_id and re-enter the init path.
      if (refreshRes.status === 404 || refreshRes.status === 409) {
        updateItem(item.key, { doc_id: undefined });
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
        ({ doc_id, upload_url } = (await initRes.json()) as {
          doc_id: string;
          upload_url: string;
        });
      } else {
        const body = await refreshRes.json().catch(() => ({}));
        throw new Error(body.detail ?? `Retry failed (${refreshRes.status})`);
      }
    } else {
      ({ doc_id, upload_url } = (await refreshRes.json()) as {
        doc_id: string;
        upload_url: string;
      });
    }
  } else {
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
    ({ doc_id, upload_url } = (await initRes.json()) as {
      doc_id: string;
      upload_url: string;
    });
  }
  updateItem(item.key, { doc_id });

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

export function UploadProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [dialogOpen, setDialogOpenState] = useState(false);

  // Per-item AbortController so cancel() works at any granularity, plus a
  // ref-mirror of items so the long-running runner closure can re-read the
  // latest queue without being recreated on every state change.
  const controllers = useRef<Map<string, AbortController>>(new Map());
  const itemsRef = useRef<UploadItem[]>(items);
  const runnerActive = useRef(false);

  // Keep the ref in sync with committed state. React 18 flushes setState from
  // event handlers synchronously, so this assignment lands before any
  // microtask/raf the action handlers schedule below.
  itemsRef.current = items;

  const summary = useMemo<UploadSummary>(() => {
    const total = items.length;
    let done = 0;
    let failed = 0;
    let pending = 0;
    let inFlight = 0;
    for (const it of items) {
      if (it.status === "success") done++;
      else if (it.status === "error") failed++;
      else if (it.status === "pending") pending++;
      else inFlight++;
    }
    return { total, done, failed, pending, inFlight };
  }, [items]);

  const updateItem = useCallback(
    (key: string, patch: Partial<UploadItem>) => {
      setItems((prev) =>
        prev.map((it) => (it.key === key ? { ...it, ...patch } : it)),
      );
    },
    [],
  );

  const enqueue = useCallback(
    (input: File[] | FileList): { added: number; rejected: number } => {
      const incoming = Array.from(input);
      const accepted: UploadItem[] = [];
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

      let added = 0;
      setItems((prev) => {
        const seen = new Set(prev.map((it) => it.key));
        const fresh = accepted.filter((it) => !seen.has(it.key));
        added = fresh.length;
        const next = [...prev, ...fresh];
        itemsRef.current = next;
        return next;
      });
      return { added, rejected };
    },
    [],
  );

  const remove = useCallback((key: string) => {
    setItems((prev) => {
      const next = prev.filter((it) => {
        if (it.key !== key) return true;
        const inFlight =
          it.status === "preparing" ||
          it.status === "uploading" ||
          it.status === "completing";
        return inFlight; // refuse to remove in-flight items; user must cancel first
      });
      itemsRef.current = next;
      return next;
    });
  }, []);

  const clearCompleted = useCallback(() => {
    setItems((prev) => {
      const next = prev.filter(
        (it) => it.status !== "success" && it.status !== "error",
      );
      itemsRef.current = next;
      return next;
    });
  }, []);

  const cancel = useCallback((key: string) => {
    const ctrl = controllers.current.get(key);
    if (ctrl) {
      ctrl.abort();
      controllers.current.delete(key);
    }
    setItems((prev) => {
      const next = prev
        .map((it) => {
          if (it.key !== key) return it;
          // Pending items get dropped outright; in-flight ones become error
          // rows so the user sees what happened and can retry.
          if (it.status === "pending") return null;
          return {
            ...it,
            status: "error" as UploadStatus,
            error: "Upload cancelled",
          };
        })
        .filter((it): it is UploadItem => it !== null);
      itemsRef.current = next;
      return next;
    });
  }, []);

  const cancelAll = useCallback(() => {
    for (const [, ctrl] of controllers.current) ctrl.abort();
    controllers.current.clear();
    setItems((prev) => {
      const next = prev
        .map((it) => {
          if (it.status === "success") return it;
          if (it.status === "pending") return null;
          return {
            ...it,
            status: "error" as UploadStatus,
            error: "Upload cancelled",
          };
        })
        .filter((it): it is UploadItem => it !== null);
      itemsRef.current = next;
      return next;
    });
  }, []);

  const runner = useCallback(async () => {
    if (runnerActive.current) return;
    runnerActive.current = true;
    setIsRunning(true);

    try {
      // Drain the pending queue serially. We re-read itemsRef each iteration
      // so files enqueued during the run are picked up automatically.
      for (let safety = 0; safety < 10_000; safety++) {
        const next = itemsRef.current.find((it) => it.status === "pending");
        if (!next) break;

        const ctrl = new AbortController();
        controllers.current.set(next.key, ctrl);

        // Re-check after registering: a cancel() that landed between find()
        // and set() may have dropped the item entirely.
        const stillThere = itemsRef.current.find((it) => it.key === next.key);
        if (!stillThere || stillThere.status !== "pending") {
          controllers.current.delete(next.key);
          continue;
        }

        try {
          await processOne(next, ctrl.signal, updateItem);
          if (typeof window !== "undefined") {
            window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
          }
        } catch (err) {
          // If the abort was user-driven, cancel() has already patched state
          // to "error" with the cancellation message; don't overwrite it.
          if (!ctrl.signal.aborted) {
            const message =
              err instanceof Error ? err.message : "Upload failed.";
            updateItem(next.key, { status: "error", error: message });
          }
        } finally {
          controllers.current.delete(next.key);
        }
      }
    } finally {
      runnerActive.current = false;
      setIsRunning(false);
    }
  }, [updateItem]);

  const startAll = useCallback(() => {
    // Defensive: ignore re-entries while the runner is mid-flight. The button
    // already disables on isRunning, but state updates lag a React tick and
    // this prevents a rapid double-click from re-flipping in-flight rows.
    if (runnerActive.current) return;
    // Promote any prior error rows back to pending so "Retry failed" works.
    setItems((prev) => {
      const next = prev.map((it) =>
        it.status === "error"
          ? {
              ...it,
              status: "pending" as UploadStatus,
              error: undefined,
              progress: 0,
            }
          : it,
      );
      itemsRef.current = next;
      return next;
    });
    // Defer until after React commits so the runner reads the updated ref.
    // requestAnimationFrame is cheaper than setTimeout(0) and runs after the
    // current event handler's render flush in React 18.
    if (typeof window !== "undefined") {
      requestAnimationFrame(() => {
        void runner();
      });
    } else {
      void runner();
    }
  }, [runner]);

  const setDialogOpen = useCallback((open: boolean) => {
    setDialogOpenState(open);
  }, []);

  // Warn before tab close while uploads are in flight. Only registered during
  // active runs so regular page reloads stay quiet.
  useEffect(() => {
    const anyInFlight = isRunning || summary.inFlight > 0;
    if (!anyInFlight) return;

    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = ""; // Chrome legacy requirement; modern browsers ignore the value
      return "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isRunning, summary.inFlight]);

  const value = useMemo<UploadContextValue>(
    () => ({
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
    }),
    [
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
    ],
  );

  return (
    <UploadContext.Provider value={value}>{children}</UploadContext.Provider>
  );
}

export function useUploads(): UploadContextValue {
  const ctx = useContext(UploadContext);
  if (!ctx) throw new Error("useUploads must be used inside <UploadProvider>");
  return ctx;
}

export const DOCUMENTS_REFRESH_EVENT = REFRESH_EVENT;
