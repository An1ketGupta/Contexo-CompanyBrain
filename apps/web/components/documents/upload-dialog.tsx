"use client";

import { useRef, useState } from "react";

const ACCEPTED_TYPES = ".pdf,.docx,.txt,.md";
const MAX_MB = 50;
const MAX_BYTES = MAX_MB * 1024 * 1024;

interface UploadDialogProps {
  onUploadComplete?: () => void;
}

type UploadPhase = "idle" | "preparing" | "uploading" | "completing" | "success" | "error";

function uploadWithProgress(
  url: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`Storage upload failed (${xhr.status})`));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Network error during upload")));
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));

    // PUT directly to Supabase Storage signed URL
    xhr.open("PUT", url);
    xhr.setRequestHeader("content-type", file.type || "application/octet-stream");
    xhr.send(file);
  });
}

export function UploadDialog({ onUploadComplete }: UploadDialogProps) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [errorMsg, setErrorMsg] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0] ?? null;
    setFile(selected);
    setErrorMsg("");
    setPhase("idle");
    setProgress(0);
  }

  function openDialog() {
    setFile(null);
    setErrorMsg("");
    setPhase("idle");
    setProgress(0);
    setOpen(true);
  }

  function closeDialog() {
    if (phase === "uploading" || phase === "preparing" || phase === "completing") return;
    setOpen(false);
  }

  async function handleUpload() {
    if (!file) return;

    if (file.size > MAX_BYTES) {
      setErrorMsg(`File too large. Maximum size is ${MAX_MB} MB.`);
      return;
    }

    setErrorMsg("");
    setProgress(0);

    try {
      // Phase 1: Get signed URL from FastAPI
      setPhase("preparing");
      const initRes = await fetch("/api/documents/upload", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          content_type: file.type || "application/octet-stream",
          file_size: file.size,
        }),
      });

      if (!initRes.ok) {
        const body = await initRes.json().catch(() => ({}));
        throw new Error(body.detail ?? `Upload init failed (${initRes.status})`);
      }

      const { doc_id, upload_url } = (await initRes.json()) as {
        doc_id: string;
        upload_url: string;
      };

      // Phase 2: Upload file directly to Supabase Storage (real progress)
      setPhase("uploading");
      await uploadWithProgress(upload_url, file, setProgress);

      // Phase 3: Notify server to trigger processing
      setPhase("completing");
      const completeRes = await fetch("/api/documents/upload/complete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ doc_id }),
      });

      if (!completeRes.ok) {
        const body = await completeRes.json().catch(() => ({}));
        throw new Error(body.detail ?? `Failed to start processing (${completeRes.status})`);
      }

      setPhase("success");
      onUploadComplete?.();
      setTimeout(() => {
        setOpen(false);
        setPhase("idle");
        setFile(null);
        setProgress(0);
      }, 1400);
    } catch (err) {
      setPhase("error");
      setErrorMsg(err instanceof Error ? err.message : "Upload failed. Please try again.");
    }
  }

  const isActive = phase === "preparing" || phase === "uploading" || phase === "completing";

  const phaseLabel: Record<UploadPhase, string> = {
    idle: "Upload",
    preparing: "Preparing…",
    uploading: `Uploading ${progress}%`,
    completing: "Finishing…",
    success: "Done!",
    error: "Retry",
  };

  return (
    <>
      <button
        onClick={openDialog}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
      >
        Upload Document
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg border border-border bg-background p-6 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-foreground">Upload Document</h2>
              <button
                onClick={closeDialog}
                disabled={isActive}
                className="text-muted-foreground hover:text-foreground disabled:opacity-40"
                aria-label="Close"
              >
                ✕
              </button>
            </div>

            {/* File picker */}
            <div
              onClick={() => !isActive && inputRef.current?.click()}
              className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-8 text-center transition-colors ${
                file
                  ? "border-primary bg-primary/5"
                  : "border-input hover:border-primary hover:bg-muted/50"
              } ${isActive ? "pointer-events-none opacity-60" : ""}`}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_TYPES}
                onChange={handleFileChange}
                className="hidden"
              />
              {file ? (
                <>
                  <p className="text-sm font-medium text-foreground">{file.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {(file.size / 1024 / 1024).toFixed(2)} MB — click to change
                  </p>
                </>
              ) : (
                <>
                  <p className="text-sm text-muted-foreground">Click to select a file</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    PDF, DOCX, TXT, MD — up to {MAX_MB} MB
                  </p>
                </>
              )}
            </div>

            {/* Progress bar — visible during upload */}
            {phase === "uploading" && (
              <div className="mt-3">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all duration-150"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <p className="mt-1 text-right text-xs text-muted-foreground">{progress}%</p>
              </div>
            )}

            {/* Status messages */}
            {errorMsg && (
              <p className="mt-3 text-sm text-destructive">{errorMsg}</p>
            )}
            {phase === "success" && (
              <p className="mt-3 text-sm text-green-600">
                Uploaded! Processing will start shortly.
              </p>
            )}

            {/* Actions */}
            <div className="mt-5 flex justify-end gap-3">
              <button
                onClick={closeDialog}
                disabled={isActive}
                className="rounded-md border border-input px-4 py-2 text-sm text-foreground hover:bg-muted disabled:opacity-40 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUpload}
                disabled={!file || isActive || phase === "success"}
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {phaseLabel[phase]}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
