"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { useCurrentUser } from "@/hooks/use-user";
import { createClient } from "@/lib/supabase/client";
import { networkError, parseApiError, reportApiError } from "@/lib/errors";

export default function SettingsPage() {
  const { user, organization, loading, refresh } = useCurrentUser();

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Workspace details, your account, and the danger zone.
        </p>
      </header>

      <WorkspaceCard
        organization={organization}
        canEdit={user?.role === "admin"}
        onSaved={refresh}
      />

      <ProfileCard
        displayName={user?.display_name ?? ""}
        email={user?.email ?? ""}
        onSaved={refresh}
      />

      <DangerZoneCard organizationName={organization?.name ?? ""} />
    </div>
  );
}

// ── Card primitive ──────────────────────────────────────────────────────────

function Card({
  title,
  description,
  children,
  tone = "default",
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  tone?: "default" | "destructive";
}) {
  return (
    <section
      className={
        tone === "destructive"
          ? "rounded-lg border border-destructive/30 bg-background"
          : "rounded-lg border border-border bg-background"
      }
    >
      <header
        className={
          tone === "destructive"
            ? "border-b border-destructive/20 px-5 py-4"
            : "border-b border-border px-5 py-4"
        }
      >
        <h2 className="text-sm font-semibold tracking-tight text-foreground">
          {title}
        </h2>
        {description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        )}
      </header>
      <div className="space-y-4 px-5 py-4">{children}</div>
    </section>
  );
}

// ── Workspace ───────────────────────────────────────────────────────────────

function WorkspaceCard({
  organization,
  canEdit,
  onSaved,
}: {
  organization: { id: string; name: string; slug: string } | null;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const [name, setName] = useState(organization?.name ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setName(organization?.name ?? "");
  }, [organization?.name]);

  if (!organization) {
    return (
      <Card title="Workspace">
        <p className="text-sm text-muted-foreground">No workspace yet.</p>
      </Card>
    );
  }

  const dirty = name.trim() !== organization.name.trim();

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed.length < 2) {
      toast.error("Workspace name must be at least 2 characters.");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/organizations/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      }).catch((err) => {
        throw networkError(err);
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success("Workspace updated.");
      onSaved();
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="Workspace"
      description={
        canEdit
          ? "Change how your workspace appears across the product."
          : "Only workspace admins can change these settings."
      }
    >
      <div className="grid gap-1.5">
        <Label htmlFor="org-name">Name</Label>
        <Input
          id="org-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          disabled={!canEdit || saving}
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="org-slug">Workspace identifier</Label>
        <Input id="org-slug" value={organization.slug} readOnly disabled />
        <p className="text-xs text-muted-foreground">
          Slug can&apos;t be changed once a workspace is created.
        </p>
      </div>

      <div className="flex justify-end">
        <Button onClick={save} disabled={!canEdit || !dirty || saving}>
          {saving && <Loader2 className="animate-spin" />}
          Save changes
        </Button>
      </div>
    </Card>
  );
}

// ── Profile ─────────────────────────────────────────────────────────────────

function ProfileCard({
  displayName,
  email,
  onSaved,
}: {
  displayName: string;
  email: string;
  onSaved: () => void;
}) {
  const [name, setName] = useState(displayName);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setName(displayName);
  }, [displayName]);

  const dirty = name.trim() !== displayName.trim();

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error("Display name cannot be empty.");
      return;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/users/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: trimmed }),
      }).catch((err) => {
        throw networkError(err);
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success("Profile updated.");
      onSaved();
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Your account" description="How you appear to your teammates.">
      <div className="grid gap-1.5">
        <Label htmlFor="display-name">Display name</Label>
        <Input
          id="display-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          disabled={saving}
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="email">Email</Label>
        <Input id="email" value={email} readOnly disabled />
        <p className="text-xs text-muted-foreground">
          Your sign-in email can&apos;t be changed yet — contact support to update it.
        </p>
      </div>

      <div className="flex justify-end">
        <Button onClick={save} disabled={!dirty || saving}>
          {saving && <Loader2 className="animate-spin" />}
          Save changes
        </Button>
      </div>
    </Card>
  );
}

// ── Danger zone ─────────────────────────────────────────────────────────────

function DangerZoneCard({ organizationName }: { organizationName: string }) {
  const router = useRouter();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [typedName, setTypedName] = useState("");
  const [deleting, setDeleting] = useState(false);

  const matches =
    typedName.trim().toLowerCase() === organizationName.trim().toLowerCase() &&
    organizationName.trim().length > 0;

  const close = () => {
    if (deleting) return;
    setConfirmOpen(false);
    setTypedName("");
  };

  const handleDelete = async () => {
    if (!matches) return;
    setDeleting(true);
    try {
      const res = await fetch("/api/users/me", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_org_name: typedName.trim() }),
      }).catch((err) => {
        throw networkError(err);
      });

      if (!res.ok && res.status !== 204) throw await parseApiError(res);

      const supabase = createClient();
      await Promise.all([
        fetch("/api/auth/logout", { method: "POST" }),
        supabase.auth.signOut(),
      ]);

      toast.success("Your account has been deleted.");
      router.replace("/login");
      router.refresh();
    } catch (err) {
      setDeleting(false);
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    }
  };

  return (
    <Card
      title="Danger zone"
      description="Irreversible actions. Make sure you mean it."
      tone="destructive"
    >
      <div className="flex flex-col gap-3 rounded-md border border-destructive/20 bg-red-50/40 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <p className="text-sm font-medium text-foreground">Delete account</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Permanently delete your account. If you&apos;re the only admin of
              this workspace, the workspace and all its documents will be
              deleted too. This can&apos;t be undone.
            </p>
          </div>
        </div>
        <Button
          variant="destructive"
          onClick={() => setConfirmOpen(true)}
          className="self-start sm:self-auto"
        >
          Delete account
        </Button>
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={(o) => !o && close()}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete your account?</AlertDialogTitle>
            <AlertDialogDescription>
              Type the workspace name <strong className="font-medium text-foreground">{organizationName}</strong>{" "}
              to confirm. We&apos;ll sign you out and you won&apos;t be able to
              log back in with this email.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <Input
            value={typedName}
            onChange={(e) => setTypedName(e.target.value)}
            placeholder={organizationName}
            disabled={deleting}
            autoFocus
          />

          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleDelete();
              }}
              disabled={!matches || deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
            >
              {deleting && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
              I understand, delete my account
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
