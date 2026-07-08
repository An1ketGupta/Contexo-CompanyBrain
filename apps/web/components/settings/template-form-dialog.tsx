"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { useTemplates } from "@/hooks/use-templates";
import { useCurrentUser } from "@/hooks/use-user";
import type { PromptTemplate, TemplateCategory } from "@/lib/types";

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
  template?: PromptTemplate | null;
}

export function TemplateFormDialog({ open, onOpenChange, template }: Props) {
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const { createTemplate, updateTemplate } = useTemplates();

  const isEdit = !!template;

  const [title, setTitle] = useState("");
  const [category, setCategory] = useState<TemplateCategory>("Other");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [isShared, setIsShared] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle(template?.title ?? "");
    setCategory(template?.category ?? "Other");
    setDescription(template?.description ?? "");
    setBody(template?.template_text ?? "");
    setIsShared(template?.is_shared ?? false);
  }, [open, template]);

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
      const payload = {
        title: title.trim(),
        description: description.trim() || null,
        template_text: body.trim(),
        category,
        is_shared: isAdmin ? isShared : false,
      };
      if (isEdit && template) {
        await updateTemplate(template.id, payload);
        toast.success("Template updated.");
      } else {
        await createTemplate(payload);
        toast.success("Template saved.");
      }
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
          <DialogTitle>{isEdit ? "Edit template" : "New template"}</DialogTitle>
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
                className="h-10 w-full rounded-[10px] border border-input bg-background px-3.5 text-sm shadow-sm transition-colors focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
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
                <Label htmlFor="tpl-share">Share with team</Label>
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
              rows={8}
              maxLength={8000}
              placeholder={`Write a email to {{person}} about {{topic}}…`}
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
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : isEdit ? (
              "Save"
            ) : (
              "Create template"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
