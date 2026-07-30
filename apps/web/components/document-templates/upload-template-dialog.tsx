"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { AlertTriangle, Loader2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useDocumentTemplates } from "@/hooks/use-document-templates";
import type { DocumentType } from "@/lib/types";

const MAX_BYTES = 25 * 1024 * 1024;

export function UploadTemplateDialog({
  open,
  onOpenChange,
  types,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  types: DocumentType[];
}) {
  const router = useRouter();
  const { createTemplate } = useDocumentTemplates();
  const fileInput = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [documentTypeId, setDocumentTypeId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setDescription("");
    setDocumentTypeId("");
    setFile(null);
    setError(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  function pickFile(selected: File | null) {
    setError(null);
    if (!selected) {
      setFile(null);
      return;
    }
    if (selected.size > MAX_BYTES) {
      setError("That file is over 25 MB. Try removing large embedded images.");
      return;
    }
    setFile(selected);
    // Default the template name to the filename — HR almost always wants that,
    // and an empty required field is friction for no reason.
    if (!name) setName(selected.name.replace(/\.(docx|pdf)$/i, ""));
  }

  async function submit() {
    if (!file || !name.trim() || !documentTypeId) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createTemplate({
        name: name.trim(),
        documentTypeId,
        description: description.trim() || undefined,
        file,
      });
      onOpenChange(false);
      reset();
      // Straight into the builder: an uploaded template nobody reviews is not
      // yet usable, so the next step should not need to be found.
      router.push(`/document-templates/${created.id}/fields`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = Boolean(file && name.trim() && documentTypeId && !submitting);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Upload a template</DialogTitle>
          <DialogDescription>
            We detect the personalized fields. Your original template is never modified.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="template-file">Word or PDF file</Label>
            <Input
              id="template-file"
              ref={fileInput}
              type="file"
              accept=".docx,.pdf"
              onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <p className="text-xs text-muted-foreground">
                {file.name} · {(file.size / 1024).toFixed(0)} KB
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">
                Automatic field detection works on Word (.docx) files. PDFs are
                stored, but you&rsquo;ll add their fields by hand.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="template-type">Document type</Label>
            <Select value={documentTypeId} onValueChange={setDocumentTypeId}>
              <SelectTrigger id="template-type">
                <SelectValue placeholder="Choose a type" />
              </SelectTrigger>
              <SelectContent>
                {types.map((type) => (
                  <SelectItem key={type.id} value={type.id}>
                    {type.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="template-name">Name</Label>
            <Input
              id="template-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Offer Letter — Engineering"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="template-description">Description (optional)</Label>
            <Textarea
              id="template-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="When this version should be used."
              rows={2}
            />
          </div>

          {error ? (
            <div className="flex items-start gap-2 rounded-lg bg-destructive-soft px-3 py-2 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Upload className="mr-2 h-4 w-4" />
            )}
            Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
