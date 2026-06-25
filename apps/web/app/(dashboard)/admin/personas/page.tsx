"use client";

import { useState } from "react";
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  Pencil,
  Plus,
  Trash2,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useOrgPersonas, type OrgPersona } from "@/hooks/use-org-personas";

export default function AdminPersonasPage() {
  const { personas, loading, error, refresh } = useOrgPersonas({
    includeArchived: true,
  });
  const [editing, setEditing] = useState<OrgPersona | "new" | null>(null);
  const [deleting, setDeleting] = useState<OrgPersona | null>(null);

  const active = personas.filter((p) => !p.is_archived);
  const archived = personas.filter((p) => p.is_archived);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Shared personas</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Define AI personas any user in the org can adopt. Each persona is
            a system-prompt overlay applied to that user&apos;s chat turns —
            tone, structure, retrieval bias. Personas live alongside (not
            instead of) the 6 built-ins.
          </p>
        </div>
        <Button onClick={() => setEditing("new")}>
          <Plus className="mr-1.5 h-4 w-4" /> New persona
        </Button>
      </header>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Could not load personas. Refresh to retry.</span>
        </div>
      ) : null}

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : (
        <>
          {active.length === 0 && archived.length === 0 ? (
            <EmptyState onCreate={() => setEditing("new")} />
          ) : (
            <section className="space-y-3">
              {active.map((p) => (
                <PersonaRow
                  key={p.id}
                  persona={p}
                  onEdit={() => setEditing(p)}
                  onDelete={() => setDeleting(p)}
                  onRefresh={refresh}
                />
              ))}
            </section>
          )}

          {archived.length > 0 && (
            <section className="space-y-3 pt-4">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Archived
              </h2>
              {archived.map((p) => (
                <PersonaRow
                  key={p.id}
                  persona={p}
                  archived
                  onEdit={() => setEditing(p)}
                  onDelete={() => setDeleting(p)}
                  onRefresh={refresh}
                />
              ))}
            </section>
          )}
        </>
      )}

      {editing && (
        <PersonaDialog
          persona={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}

      <AlertDialog open={!!deleting} onOpenChange={(v) => !v && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this persona?</AlertDialogTitle>
            <AlertDialogDescription>
              Users currently using &ldquo;{deleting?.name}&rdquo; will fall back
              to no persona on their next chat turn. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={async () => {
                if (!deleting) return;
                const res = await fetch(
                  `/api/org-personas/${encodeURIComponent(deleting.id)}`,
                  { method: "DELETE" },
                );
                if (!res.ok) {
                  toast.error("Could not delete persona.");
                  return;
                }
                toast.success("Persona deleted.");
                setDeleting(null);
                await refresh();
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-6 py-10 text-center">
      <Users className="mx-auto h-8 w-8 text-muted-foreground" />
      <h3 className="mt-3 text-sm font-medium">No shared personas yet</h3>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
        Create one for any function your team has that doesn&apos;t fit the
        built-in roles — Customer Success, Legal, Research, Marketing.
      </p>
      <Button size="sm" className="mt-4" onClick={onCreate}>
        <Plus className="mr-1.5 h-4 w-4" /> Create persona
      </Button>
    </div>
  );
}

function PersonaRow({
  persona,
  archived,
  onEdit,
  onDelete,
  onRefresh,
}: {
  persona: OrgPersona;
  archived?: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onRefresh: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);

  const toggleArchive = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/org-personas/${encodeURIComponent(persona.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_archived: !persona.is_archived }),
      });
      if (!res.ok) {
        toast.error("Could not update persona.");
        return;
      }
      toast.success(persona.is_archived ? "Persona restored." : "Persona archived.");
      await onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`rounded-lg border border-border bg-card p-4 ${
        archived ? "opacity-70" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{persona.name}</h3>
          {persona.description && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {persona.description}
            </p>
          )}
          <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs text-foreground/80">
            {persona.instructions}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={onEdit}
            disabled={busy}
            aria-label="Edit"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void toggleArchive()}
            disabled={busy}
            aria-label={archived ? "Restore" : "Archive"}
          >
            {archived ? (
              <ArchiveRestore className="h-3.5 w-3.5" />
            ) : (
              <Archive className="h-3.5 w-3.5" />
            )}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onDelete}
            disabled={busy}
            aria-label="Delete"
          >
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function PersonaDialog({
  persona,
  onClose,
  onSaved,
}: {
  persona: OrgPersona | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(persona?.name ?? "");
  const [description, setDescription] = useState(persona?.description ?? "");
  const [instructions, setInstructions] = useState(persona?.instructions ?? "");
  const [saving, setSaving] = useState(false);

  const canSave =
    name.trim().length >= 1 &&
    name.trim().length <= 80 &&
    instructions.trim().length >= 10 &&
    instructions.trim().length <= 2000;

  const save = async () => {
    setSaving(true);
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || null,
        instructions: instructions.trim(),
      };
      const res = persona
        ? await fetch(`/api/org-personas/${encodeURIComponent(persona.id)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          })
        : await fetch("/api/org-personas", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { message?: string };
        toast.error(data.message ?? "Could not save persona.");
        return;
      }
      toast.success(persona ? "Persona updated." : "Persona created.");
      await onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-xl">
        <h2 className="text-lg font-semibold">
          {persona ? "Edit persona" : "New shared persona"}
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Available to every user in your org. Changes apply on their next chat
          turn.
        </p>

        <div className="mt-4 space-y-4">
          <div className="space-y-2">
            <Label htmlFor="p-name">Name</Label>
            <Input
              id="p-name"
              value={name}
              maxLength={80}
              placeholder="e.g. Customer Success Manager"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="p-desc">
              Short description{" "}
              <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="p-desc"
              value={description ?? ""}
              maxLength={240}
              placeholder="One-line summary shown in the picker."
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="p-instr">
              Instructions{" "}
              <span className="text-muted-foreground">
                ({instructions.length}/2000)
              </span>
            </Label>
            <Textarea
              id="p-instr"
              value={instructions}
              maxLength={2000}
              rows={8}
              placeholder="USER ROLE: Customer Success. Bias retrieval toward… Format answers as… Tone…"
              onChange={(e) => setInstructions(e.target.value)}
            />
            <p className="text-[11px] text-muted-foreground">
              Tip: the most useful overlays state what to bias retrieval
              toward, the answer format, and the tone in 3-5 sentences.
            </p>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={() => void save()} disabled={!canSave || saving}>
            {saving ? "Saving…" : persona ? "Save changes" : "Create persona"}
          </Button>
        </div>
      </div>
    </div>
  );
}
