"use client";

import { useState } from "react";
import { Globe2, Loader2, Lock } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import type { Document } from "@/lib/types";

interface ShareTranscriptDialogProps {
  document: Document;
  onUpdateVisibility: (id: string, visibility: "private" | "org") => Promise<void>;
}

/**
 * Publish/unpublish control for private meeting transcripts (Google
 * Meet auto-sync, migrations 084/086). Private is the ingest default — this
 * is the only way an owner can make one visible to the rest of the org.
 */
export function ShareTranscriptDialog({
  document,
  onUpdateVisibility,
}: ShareTranscriptDialogProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const isPrivate = document.visibility === "private";

  async function handleConfirm(e: React.MouseEvent) {
    e.preventDefault();
    setPending(true);
    try {
      const next = isPrivate ? "org" : "private";
      await onUpdateVisibility(document.id, next);
      toast.success(
        next === "org"
          ? `"${document.name}" is now visible to your workspace.`
          : `"${document.name}" is private again.`,
      );
      setOpen(false);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to update visibility.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(next) => !pending && setOpen(next)}>
      <AlertDialogTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-[10px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        >
          {isPrivate ? (
            <>
              <Lock className="h-2.5 w-2.5" /> Private · Share
            </>
          ) : (
            <>
              <Globe2 className="h-2.5 w-2.5" /> Shared · Make private
            </>
          )}
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {isPrivate ? "Share this transcript with your workspace?" : "Make this transcript private again?"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {isPrivate ? (
              <>
                <span className="font-medium text-foreground">{document.name}</span>{" "}
                and its meeting summary (decisions, action items) will become
                visible and searchable by everyone in your workspace, not just you.
              </>
            ) : (
              <>
                <span className="font-medium text-foreground">{document.name}</span>{" "}
                and its meeting summary will go back to being visible only to you.
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={handleConfirm} disabled={pending}>
            {pending && <Loader2 className="animate-spin" />}
            {pending ? "Saving…" : isPrivate ? "Share with workspace" : "Make private"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
