"use client";

import { useRef, useState } from "react";
import { Loader2, Upload, UploadCloud } from "lucide-react";
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
import { cn } from "@/lib/utils";

const ACCEPTED_TYPES = ".pdf,.docx,.txt,.md";
const MAX_MB = 50;
const MAX_BYTES = MAX_MB * 1024 * 1024;

interface UploadDialogProps {
  onUploadComplete?: () => void;
}

type UploadPhase =
  | "idle"
  | "preparing"
  | "uploading"
  | "completing"
  | "success"
  | "error";

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
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Storage upload failed (${xhr.status})`));
    });
    xhr.addEventListener("error", () =>
      reject(new Error("Network error during upload")),
    );
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));

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
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const isActive =
    phase === "preparing" || phase === "uploading" || phase === "completing";

  function reset() {
    setFile(null);
    setPhase("idle");
    setProgress(0);
    setDragOver(false);
  }

  function handleOpenChange(next: boolean) {
    if (isActive) return; // Block close mid-upload
    setOpen(next);
    if (!next) reset();
  }

  function pickFile(selected: File | null) {
    if (!selected) return;
    if (selected.size > MAX_BYTES) {
      toast.error(`File too large. Maximum size is ${MAX_MB} MB.`);
      return;
    }
    setFile(selected);
    setPhase("idle");
    setProgress(0);
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (isActive) return;
    pickFile(e.dataTransfer.files?.[0] ?? null);
  }

  async function handleUpload() {
    if (!file) return;
    setProgress(0);

    try {
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
        throw new Error(
          body.detail ?? `Upload init failed (${initRes.status})`,
        );
      }

      const { doc_id, upload_url } = (await initRes.json()) as {
        doc_id: string;
        upload_url: string;
      };

      setPhase("uploading");
      await uploadWithProgress(upload_url, file, setProgress);

      setPhase("completing");
      const completeRes = await fetch("/api/documents/upload/complete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ doc_id }),
      });

      if (!completeRes.ok) {
        const body = await completeRes.json().catch(() => ({}));
        throw new Error(
          body.detail ?? `Failed to start processing (${completeRes.status})`,
        );
      }

      setPhase("success");
      toast.success(`Uploaded "${file.name}". Processing started.`);
      onUploadComplete?.();
      // Close after a beat so the user sees the success state.
      setTimeout(() => {
        setOpen(false);
        reset();
      }, 900);
    } catch (err) {
      setPhase("error");
      toast.error(
        err instanceof Error ? err.message : "Upload failed. Please try again.",
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <Upload />
          Upload document
        </Button>
      </DialogTrigger>
      <DialogContent showClose={!isActive}>
        <DialogHeader>
          <DialogTitle>Upload document</DialogTitle>
          <DialogDescription>
            PDF, DOCX, TXT, or MD. Up to {MAX_MB} MB. Processing starts as soon
            as the upload completes.
          </DialogDescription>
        </DialogHeader>

        <div
          onClick={() => !isActive && inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            if (!isActive) setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-10 text-center transition-colors",
            file
              ? "border-primary bg-primary/5"
              : dragOver
                ? "border-primary bg-primary/10"
                : "border-input hover:border-primary/60 hover:bg-muted/40",
            isActive && "pointer-events-none opacity-60",
          )}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            className="hidden"
          />
          <UploadCloud className="mb-2 h-6 w-6 text-muted-foreground" />
          {file ? (
            <>
              <p className="text-sm font-medium text-foreground">{file.name}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {(file.size / 1024 / 1024).toFixed(2)} MB — click to change
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-medium text-foreground">
                Drag a file or click to select
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                PDF, DOCX, TXT, MD — up to {MAX_MB} MB
              </p>
            </>
          )}
        </div>

        {phase === "uploading" && (
          <div className="space-y-1">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all duration-150"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-right text-xs text-muted-foreground">
              {progress}%
            </p>
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isActive}
          >
            Cancel
          </Button>
          <Button
            onClick={handleUpload}
            disabled={!file || isActive || phase === "success"}
          >
            {isActive && <Loader2 className="animate-spin" />}
            {phase === "preparing"
              ? "Preparing…"
              : phase === "uploading"
                ? `Uploading ${progress}%`
                : phase === "completing"
                  ? "Finishing…"
                  : phase === "success"
                    ? "Done"
                    : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
