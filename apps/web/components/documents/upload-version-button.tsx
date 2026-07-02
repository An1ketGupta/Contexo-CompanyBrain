"use client";

import { useRef, useState } from "react";
import { mutate as globalMutate } from "swr";
import { Loader2, UploadCloud } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

interface UploadVersionButtonProps {
  documentId: string;
  documentName: string;
  /**
   * Called after the new version is queued for processing. Use it to
   * invalidate the documents list / version history so the row reflects
   * `status: processing` immediately.
   */
  onUploaded?: () => void;
  className?: string;
}

const ACCEPTED = ".pdf,.docx,.txt,.md,.html,.csv,.xlsx,.pptx";

/**
 * Admin-only "New version" affordance. Three-step PUT-upload pattern matches
 * the original upload flow: init → PUT to signed URL → complete.
 */
export function UploadVersionButton({
  documentId,
  documentName,
  onUploaded,
  className,
}: UploadVersionButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const handleFile = async (file: File) => {
    setUploading(true);
    try {
      const init = await fetch(
        `/api/documents/${documentId}/versions/init`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename: file.name,
            content_type: file.type || "application/octet-stream",
            file_size: file.size,
          }),
        },
      );
      if (!init.ok) {
        const body = await init.json().catch(() => ({}));
        throw new Error(body?.detail || `init failed (${init.status})`);
      }
      const { version_id, upload_url, version_number } = (await init.json()) as {
        version_id: string;
        upload_url: string;
        version_number: number;
        storage_path: string;
        file_type: string;
      };

      const put = await fetch(upload_url, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": file.type || "application/octet-stream" },
      });
      if (!put.ok) {
        throw new Error(`Upload to storage failed (${put.status})`);
      }

      const complete = await fetch(
        `/api/documents/${documentId}/versions/complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version_id }),
        },
      );
      if (!complete.ok) {
        const body = await complete.json().catch(() => ({}));
        throw new Error(body?.detail || `complete failed (${complete.status})`);
      }

      toast.success(
        `Uploaded v${version_number} of "${documentName}". Re-processing…`,
      );
      await Promise.allSettled([
        globalMutate("/api/documents"),
        globalMutate(`/api/documents/${documentId}/versions`),
        globalMutate(`/api/documents/${documentId}/diffs`),
      ]);
      onUploaded?.();
    } catch (err) {
      toast.error((err as Error).message || "Couldn't upload new version.");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void handleFile(file);
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={uploading}
        className={cn(
          "inline-flex items-center gap-1 text-xs font-semibold text-brand hover:underline disabled:opacity-50",
          className,
        )}
        title="Upload a new version of this document"
      >
        {uploading ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <UploadCloud className="h-3 w-3" />
        )}
        New version
      </button>
    </>
  );
}
