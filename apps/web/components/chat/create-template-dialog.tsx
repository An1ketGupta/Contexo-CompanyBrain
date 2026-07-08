"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
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
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { useTemplates } from "@/hooks/use-templates";
import { useCurrentUser } from "@/hooks/use-user";
import type { TemplateCategory } from "@/lib/types";

const CATEGORIES: TemplateCategory[] = [
  "Email",
  "Job Description",
  "Announcement",
  "Policy Q&A",
  "Meeting Prep",
  "Customer Response",
  "Slack Reply",
  "Other",
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Pre-fill the template body — used by "Save as template" on a message. */
  initialText?: string;
  initialTitle?: string;
  initialCategory?: TemplateCategory;
}

/**
 * Modal for creating a personal or org-shared template.
 *
 * The "Shared with team" toggle is gated to admins. Members can create
 * private templates only — keeping a shared library tidy is an admin job,
 * and otherwise any joiner could clutter every teammate's popover.
 */
export function CreateTemplateDialog({
  open,
  onOpenChange,
  initialText = "",
  initialTitle = "",
  initialCategory = "Other",
}: Props) {
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const { createTemplate } = useTemplates();

  const [title, setTitle] = useState(initialTitle);
  const [category, setCategory] = useState<TemplateCategory>(initialCategory);
  const [description, setDescription] = useState("");
  const [body, setBody] = useState(initialText);
  const [isShared, setIsShared] = useState(false);
  const [saving, setSaving] = useState(false);

  // Reset form whenever the dialog opens or the prefill changes — opening
  // it after a previous save shouldn't show the last value.
  useEffect(() => {
    if (open) {
      setTitle(initialTitle);
      setCategory(initialCategory);
      setBody(initialText);
      setDescription("");
      setIsShared(false);
    }
  }, [open, initialTitle, initialCategory, initialText]);

  const canSave =
    !saving &&
    title.trim().length > 0 &&
    title.trim().length <= 120 &&
    body.trim().length > 0 &&
    body.trim().length <= 8000;

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      await createTemplate({
        title: title.trim(),
        description: description.trim() || null,
        template_text: body.trim(),
        category,
        is_shared: isAdmin ? isShared : false,
      });
      toast.success("Template saved.");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save template");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Save as template</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="tpl-title">Title</Label>
            <Input
              id="tpl-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={120}
              placeholder="Write a sales follow-up"
              autoFocus
            />
          </div>

          <div className={isAdmin ? "grid grid-cols-2 gap-3" : undefined}>
            <div className="space-y-1">
              <Label htmlFor="tpl-cat">Category</Label>
              <select
                id="tpl-cat"
                value={category}
                onChange={(e) => setCategory(e.target.value as TemplateCategory)}
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            {isAdmin && (
              <div className="flex items-end gap-2 pb-1">
                <Checkbox
                  id="tpl-share"
                  checked={isShared}
                  onCheckedChange={(v) => setIsShared(Boolean(v))}
                />
                <Label htmlFor="tpl-share">Shared with team</Label>
              </div>
            )}
          </div>

          <div className="space-y-1">
            <Label htmlFor="tpl-desc">Description (optional)</Label>
            <Input
              id="tpl-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={280}
              placeholder="One-line context for teammates"
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="tpl-body">Prompt</Label>
            <Textarea
              id="tpl-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={6}
              maxLength={8000}
              placeholder="Write a email to [person] about [topic]…"
            />
            <p className="text-[11px] text-muted-foreground">
              {body.length.toLocaleString()} / 8,000
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave}>
            {saving ? "Saving…" : "Save template"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
