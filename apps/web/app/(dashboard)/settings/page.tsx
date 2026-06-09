"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Loader2,
  Mail,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UserPlus,
} from "lucide-react";
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

      <TeamCard isAdmin={user?.role === "admin"} />

      {user?.role === "admin" && <FeedbackSignalCard />}

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

// ── Team ────────────────────────────────────────────────────────────────────

interface Member {
  id: string;
  role: "admin" | "member";
  display_name: string | null;
  email: string | null;
  created_at: string;
}

interface PendingInvite {
  id: string;
  email: string;
  role: "admin" | "member";
  expires_at: string;
  created_at: string;
  invited_by: string | null;
}

function TeamCard({ isAdmin }: { isAdmin: boolean }) {
  const members = useSWR<{ members: Member[] }>(
    "/api/organizations/members",
    jsonFetcher,
    { revalidateOnFocus: false },
  );
  const invites = useSWR<{ invitations: PendingInvite[] }>(
    "/api/organizations/invitations",
    jsonFetcher,
    { revalidateOnFocus: false },
  );

  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [sending, setSending] = useState(false);

  const sendInvite = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed)) {
      toast.error("Enter a valid email address.");
      return;
    }
    setSending(true);
    try {
      const res = await fetch("/api/organizations/invitations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: trimmed, role }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        message?: string;
        detail?: string;
      };
      if (!res.ok) {
        toast.error(data.message ?? data.detail ?? "Failed to send invite.");
        return;
      }
      toast.success(`Invite sent to ${trimmed}.`);
      setEmail("");
      setRole("member");
      invites.mutate();
    } finally {
      setSending(false);
    }
  };

  const revokeInvite = async (id: string, inviteEmail: string) => {
    const res = await fetch(`/api/organizations/invitations/${id}`, {
      method: "DELETE",
    });
    if (!res.ok && res.status !== 204) {
      toast.error("Failed to revoke invite.");
      return;
    }
    toast.success(`Invite to ${inviteEmail} revoked.`);
    invites.mutate();
  };

  return (
    <Card
      title="Team"
      description={
        isAdmin
          ? "Invite teammates and manage roles."
          : "Members of your workspace. Only admins can invite or revoke."
      }
    >
      <div>
        <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Members
        </p>
        {members.isLoading ? (
          <MemberRowSkeleton />
        ) : members.error ? (
          <p className="text-sm text-destructive">Couldn&apos;t load members.</p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {(members.data?.members ?? []).map((m) => (
              <li
                key={m.id}
                className="flex items-center justify-between gap-3 px-3 py-2.5"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">
                    {m.display_name ?? "—"}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {m.email ?? "unknown"}
                  </p>
                </div>
                <RoleBadge role={m.role} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {(invites.data?.invitations ?? []).length > 0 && (
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Pending invites
          </p>
          <ul className="divide-y divide-border rounded-md border border-border">
            {invites.data!.invitations.map((inv) => (
              <li
                key={inv.id}
                className="flex items-center justify-between gap-3 px-3 py-2.5"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-foreground">{inv.email}</p>
                  <p className="text-xs text-muted-foreground">
                    Expires {formatDate(inv.expires_at)}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <RoleBadge role={inv.role} />
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={() => revokeInvite(inv.id, inv.email)}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-destructive"
                      aria-label="Revoke invite"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Revoke
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {isAdmin && (
        <div className="space-y-2 rounded-md border border-dashed border-border bg-muted/30 p-3">
          <Label htmlFor="invite-email" className="flex items-center gap-1.5 text-xs">
            <Mail className="h-3 w-3" />
            Invite by email
          </Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@company.com"
              disabled={sending}
              autoComplete="off"
              className="flex-1"
            />
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as "admin" | "member")}
              disabled={sending}
              className="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            <Button onClick={sendInvite} disabled={sending || !email}>
              {sending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <UserPlus className="h-3.5 w-3.5" />
              )}
              Send invite
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function RoleBadge({ role }: { role: "admin" | "member" }) {
  return (
    <span
      className={
        role === "admin"
          ? "inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary"
          : "inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
      }
    >
      {role}
    </span>
  );
}

function MemberRowSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1].map((i) => (
        <div
          key={i}
          className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5"
        >
          <div className="space-y-1.5">
            <div className="h-3 w-32 animate-pulse rounded bg-muted" />
            <div className="h-2 w-40 animate-pulse rounded bg-muted" />
          </div>
          <div className="h-4 w-12 animate-pulse rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

const jsonFetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed (${res.status})`);
  return res.json();
};

// ── Feedback signal (admin) ─────────────────────────────────────────────────

interface FeedbackStats {
  positive: number;
  negative: number;
  unrated: number;
  total: number;
}

function FeedbackSignalCard() {
  const { data, error, isLoading } = useSWR<FeedbackStats>(
    "/api/admin/feedback-stats",
    async (url: string) => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      return res.json();
    },
    { revalidateOnFocus: false },
  );

  const rated = (data?.positive ?? 0) + (data?.negative ?? 0);
  const csat =
    rated > 0 ? Math.round(((data?.positive ?? 0) / rated) * 100) : null;

  return (
    <Card
      title="Feedback signal"
      description="What your team is rating across assistant answers. Admin only."
    >
      {isLoading ? (
        <div className="flex h-16 items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : error ? (
        <p className="text-sm text-destructive">
          Couldn&apos;t load feedback stats. Try again later.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="Helpful"
            value={data?.positive ?? 0}
            icon={<ThumbsUp className="h-3.5 w-3.5 text-emerald-600" />}
          />
          <Stat
            label="Not helpful"
            value={data?.negative ?? 0}
            icon={<ThumbsDown className="h-3.5 w-3.5 text-destructive" />}
          />
          <Stat label="Unrated" value={data?.unrated ?? 0} muted />
          <Stat
            label="CSAT"
            value={csat === null ? "—" : `${csat}%`}
            hint={rated > 0 ? `${rated} rated` : "No ratings yet"}
          />
        </div>
      )}
    </Card>
  );
}

function Stat({
  label,
  value,
  icon,
  hint,
  muted,
}: {
  label: string;
  value: number | string;
  icon?: React.ReactNode;
  hint?: string;
  muted?: boolean;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </div>
      <div
        className={
          muted
            ? "mt-1 text-xl font-semibold text-muted-foreground tabular-nums"
            : "mt-1 text-xl font-semibold text-foreground tabular-nums"
        }
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-muted-foreground">{hint}</div>}
    </div>
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
