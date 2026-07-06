"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ChevronLeft,
  LayoutTemplate,
  Loader2,
  Lock,
  Pencil,
  Plus,
  Search,
  Trash2,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { TemplateFormDialog } from "@/components/settings/template-form-dialog";
import { useTemplates } from "@/hooks/use-templates";
import { useCurrentUser } from "@/hooks/use-user";
import { cn } from "@/lib/utils";
import type { PromptTemplate, TemplateCategory } from "@/lib/types";

const CATEGORIES: Array<TemplateCategory | "All"> = [
  "All",
  "Email",
  "Job Description",
  "Announcement",
  "Policy Q&A",
  "Meeting Prep",
  "Customer Response",
  "Slack Reply",
  "Other",
];

export default function TemplatesSettingsPage() {
  const { user, loading: userLoading } = useCurrentUser();
  const [category, setCategory] = useState<TemplateCategory | "All">("All");
  const [search, setSearch] = useState("");
  const { templates, loading, error, deleteTemplate } = useTemplates(
    category,
    search,
  );

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<PromptTemplate | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PromptTemplate | null>(null);
  const [deleting, setDeleting] = useState(false);

  const isAdmin = user?.role === "admin";

  const canMutate = (tpl: PromptTemplate): boolean => {
    if (tpl.is_builtin) return false;
    if (isAdmin) return true;
    return !!user?.id && tpl.created_by === user.id;
  };

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };
  const openEdit = (tpl: PromptTemplate) => {
    setEditing(tpl);
    setFormOpen(true);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteTemplate(deleteTarget.id);
      toast.success(`Deleted "${deleteTarget.title}".`);
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeleting(false);
    }
  };

  const grouped = useMemo(() => {
    if (category !== "All") return null;
    const buckets = new Map<TemplateCategory, PromptTemplate[]>();
    for (const t of templates) {
      const list = buckets.get(t.category) ?? [];
      list.push(t);
      buckets.set(t.category, list);
    }
    return buckets;
  }, [category, templates]);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <div>
        <Link
          href="/settings"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronLeft className="h-3 w-3" />
          Settings
        </Link>
        <div className="mt-2 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight">Templates</h1>
            <p className="mt-1.5 text-[15px] text-muted-foreground">
              Saved prompts your team can pick from in the chat composer.
            </p>
          </div>
          <Button size="sm" onClick={openCreate}>
            <Plus className="h-3.5 w-3.5" />
            New template
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by title"
            className="pl-8"
          />
        </div>
        <div className="flex flex-wrap gap-1">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                category === c
                  ? "border-transparent bg-brand-tint font-semibold text-brand"
                  : "border-border bg-background text-muted-foreground hover:border-foreground/40 hover:text-foreground",
              )}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      {!isAdmin && !userLoading && (
        <p className="text-[11px] text-muted-foreground">
          You can create and manage your own templates. Only admins can share a
          template with the whole team or edit builtins.
        </p>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading templates…
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-destructive/30 bg-destructive-soft px-4 py-3 text-sm text-destructive-ink">
          {error}
        </div>
      ) : templates.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-background px-6 py-12 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-tint text-brand">
            <LayoutTemplate className="h-5 w-5" />
          </div>
          <p className="mt-3 text-sm font-medium text-foreground">
            No templates match
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {search || category !== "All"
              ? "Try clearing the filter or search."
              : "Save your most-used prompts so the whole team can reuse them."}
          </p>
          <Button onClick={openCreate} size="sm" className="mt-4">
            <Plus className="h-3.5 w-3.5" />
            Create a template
          </Button>
        </div>
      ) : grouped ? (
        <div className="space-y-6">
          {CATEGORIES.filter((c): c is TemplateCategory => c !== "All")
            .filter((c) => (grouped.get(c) ?? []).length > 0)
            .map((c) => (
              <section key={c} className="space-y-2">
                <h2 className="font-mono text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
                  {c}
                </h2>
                <ul className="space-y-2">
                  {(grouped.get(c) ?? []).map((tpl) => (
                    <TemplateRow
                      key={tpl.id}
                      tpl={tpl}
                      mutable={canMutate(tpl)}
                      onEdit={openEdit}
                      onDelete={setDeleteTarget}
                    />
                  ))}
                </ul>
              </section>
            ))}
        </div>
      ) : (
        <ul className="space-y-2">
          {templates.map((tpl) => (
            <TemplateRow
              key={tpl.id}
              tpl={tpl}
              mutable={canMutate(tpl)}
              onEdit={openEdit}
              onDelete={setDeleteTarget}
            />
          ))}
        </ul>
      )}

      <TemplateFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        template={editing}
      />

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {deleteTarget?.title}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Teammates who&apos;ve used this template before will keep their
              past messages.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} disabled={deleting}>
              {deleting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Delete"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function TemplateRow({
  tpl,
  mutable,
  onEdit,
  onDelete,
}: {
  tpl: PromptTemplate;
  mutable: boolean;
  onEdit: (tpl: PromptTemplate) => void;
  onDelete: (tpl: PromptTemplate) => void;
}) {
  return (
    <li className="flex items-start gap-3 rounded-2xl border border-border bg-background px-3 py-2.5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-tint text-brand">
        <LayoutTemplate className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <p className="truncate text-sm font-medium text-foreground">
            {tpl.title}
          </p>
          {tpl.is_builtin ? (
            <Badge variant="default" className="h-4 px-1.5 text-[10px]">
              Builtin
            </Badge>
          ) : tpl.is_shared ? (
            <Badge
              variant="brand"
              className="h-4 gap-0.5 px-1.5 text-[10px]"
            >
              <Users className="h-2.5 w-2.5" />
              Team
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="h-4 gap-0.5 px-1.5 text-[10px]"
            >
              <Lock className="h-2.5 w-2.5" />
              Private
            </Badge>
          )}
        </div>
        {tpl.description ? (
          <p className="truncate text-xs text-muted-foreground">
            {tpl.description}
          </p>
        ) : null}
        <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-[11px] text-muted-foreground/80">
          {tpl.template_text}
        </p>
        <p className="mt-1 text-[10px] text-muted-foreground/70">
          {tpl.use_count.toLocaleString()} use{tpl.use_count === 1 ? "" : "s"}
          {tpl.variables.length > 0
            ? ` · ${tpl.variables.length} variable${tpl.variables.length === 1 ? "" : "s"}`
            : ""}
        </p>
      </div>
      {mutable ? (
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onEdit(tpl)}
            aria-label={`Edit ${tpl.title}`}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onDelete(tpl)}
            aria-label={`Delete ${tpl.title}`}
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ) : null}
    </li>
  );
}
