"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface RenameDialogProps {
  open: boolean;
  initialTitle: string;
  onOpenChange: (open: boolean) => void;
  onConfirm: (title: string) => Promise<void>;
}

export function RenameDialog({
  open,
  initialTitle,
  onOpenChange,
  onConfirm,
}: RenameDialogProps) {
  const [value, setValue] = useState(initialTitle);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) setValue(initialTitle);
  }, [open, initialTitle]);

  const submit = async () => {
    const trimmed = value.trim();
    if (!trimmed || trimmed === initialTitle.trim()) {
      onOpenChange(false);
      return;
    }
    setSaving(true);
    try {
      await onConfirm(trimmed);
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !saving && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename conversation</DialogTitle>
        </DialogHeader>
        <Input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          maxLength={80}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving || value.trim().length === 0}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
