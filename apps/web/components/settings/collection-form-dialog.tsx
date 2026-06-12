"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
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
import { useDocumentTags } from "@/hooks/use-documents";
import {
  useCollections,
  type Collection,
  type CollectionUpsertInput,
} from "@/hooks/use-collections";
import { cn } from "@/lib/utils";

const PRESET_COLORS = [
  "#6366f1", // indigo
  "#8b5cf6", // violet
  "#ec4899", // pink
  "#ef4444", // red
  "#f59e0b", // amber
  "#10b981", // emerald
  "#06b6d4", // cyan
  "#3b82f6", // blue
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** When supplied, the dialog edits this collection in place. */
  collection?: Collection | null;
  onSaved?: () => void;
}

/** V5 #35 — Create / edit a collection. Lives in /settings/collections.
 *
 *  The "tag picker" is a chip toggle row over the org's existing tags
 *  (`useDocumentTags`). Live preview shows how many documents the current
 *  selection matches — same logic as the backend's overlap query, computed
 *  client-side from the tags' document counts so the user gets feedback
 *  without a round-trip per keystroke.
 */
export function CollectionFormDialog({
  open,
  onOpenChange,
  collection,
  onSaved,
}: Props) {
  const isEdit = !!collection;
  const { tags: allTags } = useDocumentTags();
  const { create, update } = useCollections();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState(PRESET_COLORS[0]);
  const [icon, setIcon] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  // Reset when the dialog opens / target collection changes. Without this,
  // closing then reopening for a different row would show stale form state.
  useEffect(() => {
    if (!open) return;
    setName(collection?.name ?? "");
    setDescription(collection?.description ?? "");
    setColor(collection?.color ?? PRESET_COLORS[0]);
    setIcon(collection?.icon ?? "");
    setSelectedTags(collection?.tag_filters ?? []);
  }, [open, collection]);

  // Estimated doc count = sum of distinct doc IDs across selected tags.
  // useDocumentTags only gives us tag→count, so a doc tagged with two of
  // the selected tags is double-counted here. Good enough for a preview;
  // the row in the settings list uses the backend's exact count.
  const preview = useMemo(() => {
    if (!selectedTags.length) return 0;
    return allTags
      .filter((t) => selectedTags.includes(t.tag))
      .reduce((sum, t) => sum + t.count, 0);
  }, [allTags, selectedTags]);

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      toast.error("Name is required.");
      return;
    }
    if (selectedTags.length === 0) {
      toast.error("Pick at least one tag for this collection.");
      return;
    }
    const payload: CollectionUpsertInput = {
      name: name.trim(),
      description: description.trim() || null,
      color,
      icon: icon.trim() || null,
      tag_filters: selectedTags,
    };
    setSaving(true);
    try {
      if (isEdit && collection) {
        await update(collection.id, payload);
        toast.success("Collection updated.");
      } else {
        await create(payload);
        toast.success("Collection created.");
      }
      onOpenChange(false);
      onSaved?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save collection.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit collection" : "New collection"}</DialogTitle>
          <DialogDescription>
            Group documents by tag for scoped search and chat.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="col-name">Name</Label>
            <Input
              id="col-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Marketing, HR policies"
              maxLength={80}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="col-desc">Description (optional)</Label>
            <Textarea
              id="col-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What's in this collection?"
              maxLength={280}
              rows={2}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label>Color</Label>
              <div className="flex flex-wrap gap-1.5">
                {PRESET_COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setColor(c)}
                    style={{ backgroundColor: c }}
                    aria-label={`Pick color ${c}`}
                    className={cn(
                      "h-6 w-6 rounded-full border-2 transition-transform",
                      color === c
                        ? "scale-110 border-foreground"
                        : "border-transparent hover:scale-105",
                    )}
                  />
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="col-icon">Icon (emoji)</Label>
              <Input
                id="col-icon"
                value={icon}
                onChange={(e) => setIcon(e.target.value.slice(0, 4))}
                placeholder="📘"
                maxLength={4}
                className="text-base"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Tags in this collection</Label>
            {allTags.length === 0 ? (
              <p className="rounded-md border border-dashed border-border bg-muted/30 px-3 py-3 text-xs text-muted-foreground">
                You don&apos;t have any document tags yet. Tag a few documents
                first, then come back to bundle them into a collection.
              </p>
            ) : (
              <div className="flex max-h-44 flex-wrap gap-1.5 overflow-y-auto rounded-md border border-border bg-muted/20 p-2">
                {allTags.map((t) => {
                  const active = selectedTags.includes(t.tag);
                  return (
                    <button
                      key={t.tag}
                      type="button"
                      onClick={() => toggleTag(t.tag)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
                        active
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground",
                      )}
                    >
                      {t.tag}
                      <span className="text-[10px] opacity-70 tabular-nums">
                        {t.count}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
            <p className="text-[11px] text-muted-foreground">
              Preview: matches up to <strong>{preview}</strong> document tag
              {preview === 1 ? "" : "s"} (actual count may be lower if documents
              share tags).
            </p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : isEdit ? (
                "Save"
              ) : (
                "Create collection"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
