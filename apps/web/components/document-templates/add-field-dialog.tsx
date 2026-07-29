"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, Plus } from "lucide-react";

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
import type { DocumentDataType } from "@/lib/types";

const DATA_TYPES: { value: DocumentDataType; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "email", label: "Email" },
  { value: "phone", label: "Phone" },
  { value: "date", label: "Date" },
  { value: "currency", label: "Amount" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Yes / No" },
  { value: "address", label: "Address" },
  { value: "designation", label: "Designation" },
  { value: "department", label: "Department" },
  { value: "manager", label: "Manager" },
  { value: "company", label: "Company" },
  { value: "signature_block", label: "Signature area" },
  { value: "custom", label: "Other" },
];

/** Derive the stable identifier from what HR typed, matching the backend's
 * `^[a-z][a-z0-9_]*$` constraint so a rejected name is caught here rather than
 * as a 409 from the API. */
function toInternalName(label: string): string {
  const slug = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!slug) return "";
  return /^[0-9]/.test(slug) ? `f_${slug}` : slug;
}

export function AddFieldDialog({
  open,
  onOpenChange,
  existingNames,
  onCreate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  existingNames: string[];
  onCreate: (input: {
    internal_name: string;
    display_name: string;
    data_type: DocumentDataType;
    is_required: boolean;
    description?: string;
  }) => Promise<unknown>;
}) {
  const [label, setLabel] = useState("");
  const [dataType, setDataType] = useState<DocumentDataType>("text");
  const [required, setRequired] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const internalName = toInternalName(label);
  const duplicate = internalName !== "" && existingNames.includes(internalName);

  function reset() {
    setLabel("");
    setDataType("text");
    setRequired(true);
    setError(null);
  }

  async function submit() {
    if (!internalName || duplicate) return;
    setSubmitting(true);
    setError(null);
    try {
      await onCreate({
        internal_name: internalName,
        display_name: label.trim(),
        data_type: dataType,
        is_required: required,
      });
      onOpenChange(false);
      reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't add the field");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add a field</DialogTitle>
          <DialogDescription>
            For a detail that changes per person but wasn&rsquo;t picked up
            automatically. You&rsquo;ll still need to point it at a spot in the
            document.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="field-label">Field name</Label>
            <Input
              id="field-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Notice Period"
              autoFocus
            />
            {internalName ? (
              <p className="text-xs text-muted-foreground">
                Referred to internally as{" "}
                <code className="rounded bg-muted px-1">{internalName}</code>
              </p>
            ) : null}
            {duplicate ? (
              <p className="text-xs text-destructive">
                A field with that name already exists on this template.
              </p>
            ) : null}
          </div>

          <div className="space-y-2">
            <Label htmlFor="field-type">Type</Label>
            <Select
              value={dataType}
              onValueChange={(v) => setDataType(v as DocumentDataType)}
            >
              <SelectTrigger id="field-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATA_TYPES.map((type) => (
                  <SelectItem key={type.value} value={type.value}>
                    {type.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              The type decides how we check the value before a document is
              produced.
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={required}
              onChange={(e) => setRequired(e.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            Required — block generation if this is missing
          </label>

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
          <Button
            onClick={submit}
            disabled={!internalName || duplicate || submitting}
          >
            {submitting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            Add field
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
