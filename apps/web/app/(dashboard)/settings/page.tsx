"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Building2,
  CalendarClock,
  Check,
  ChevronRight,
  Copy,
  CreditCard,
  Download,
  FolderOpen,
  KeyRound,
  Link2,
  Loader2,
  Mail,
  MoreHorizontal,
  Plug,
  ShieldAlert,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UserCheck,
  UserMinus,
  UserPlus,
  Webhook,
  X,
} from "lucide-react";
import Link from "next/link";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentUser } from "@/hooks/use-user";
import { useOrgPersonas } from "@/hooks/use-org-personas";
import { createClient } from "@/lib/supabase/client";
import { networkError, parseApiError, reportApiError } from "@/lib/errors";

export default function SettingsPage() {
  const { user, organization, loading, refresh } = useCurrentUser();

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl space-y-8 p-6 md:p-8">
        <div className="space-y-2">
          <Skeleton className="h-6 w-28" />
          <Skeleton className="h-3.5 w-80" />
        </div>
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="space-y-4 rounded-lg border border-border bg-background p-5"
          >
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-9 w-full rounded-md" />
            <Skeleton className="h-9 w-2/3 rounded-md" />
          </div>
        ))}
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

      <TeamCard isAdmin={user?.role === "admin"} currentUserId={user?.id ?? null} />

      <AIInstructionsCard canEdit={user?.role === "admin"} />

      <CompetitorWatchlistCard canEditOrg={user?.role === "admin"} />

      <SharingCard canEdit={user?.role === "admin"} />

      <ArchiveCard canEdit={user?.role === "admin"} />

      <Card
        title="Plan & billing"
        description="Subscription, invoices, and usage limits."
      >
        <div className="-mx-1 grid gap-1">
          <SettingsLink
            href="/settings/billing"
            icon={<CreditCard className="h-4 w-4" />}
            title="Billing"
            description="Manage your plan, payment method, and invoices"
          />
        </div>
      </Card>

      {user?.role === "admin" && (
        <Card
          title="Connected systems"
          description="Bring your team's tools into the brain — and let them call it from outside."
        >
          <div className="-mx-1 grid gap-1">
            <SettingsLink
              href="/settings/integrations"
              icon={<Plug className="h-4 w-4" />}
              title="Integrations"
              description="Google Drive, Notion, Email forward-to-brain, Slack bot"
            />
            <SettingsLink
              href="/settings/webhooks"
              icon={<Webhook className="h-4 w-4" />}
              title="Webhooks"
              description="Notify external systems when documents or queries complete"
            />
            <SettingsLink
              href="/settings/collections"
              icon={<FolderOpen className="h-4 w-4" />}
              title="Collections"
              description="Named tag groups for scoping chat to a subset of documents"
            />
            <SettingsLink
              href="/settings/api"
              icon={<KeyRound className="h-4 w-4" />}
              title="API keys"
              description="Programmatic access to the Developer API"
            />
            <SettingsLink
              href="/settings/reports"
              icon={<CalendarClock className="h-4 w-4" />}
              title="Scheduled reports"
              description="Recurring usage + knowledge-health emails to your inbox"
            />
          </div>
        </Card>
      )}

      {user?.role === "admin" && <FeedbackSignalCard />}

      <ProfileCard
        displayName={user?.display_name ?? ""}
        email={user?.email ?? ""}
        onSaved={refresh}
      />

      <ChangePasswordCard />

      <PersonaCard
        persona={user?.persona ?? null}
        customName={user?.custom_persona_name ?? null}
        customInstructions={user?.custom_persona_instructions ?? null}
        onSaved={refresh}
      />

      <BriefingPreferencesCard />

      <ActivityPrivacyCard
        activityPrivate={user?.activity_private ?? false}
        onSaved={refresh}
      />

      <DataExportCard isAdmin={user?.role === "admin"} />

      <DangerZoneCard organizationName={organization?.name ?? ""} />
    </div>
  );
}

function SettingsLink({
  href,
  icon,
  title,
  description,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-3 rounded-md px-2 py-2.5 text-sm hover:bg-muted"
    >
      <div className="grid h-7 w-7 shrink-0 place-items-center rounded bg-muted text-muted-foreground">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-foreground">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <ChevronRight className="h-4 w-4 text-muted-foreground" />
    </Link>
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

// ── Change password ─────────────────────────────────────────────────────────

function ChangePasswordCard() {
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next.length < 8) {
      toast.error("Password must be at least 8 characters.");
      return;
    }
    if (next !== confirm) {
      toast.error("Passwords don't match.");
      return;
    }
    setSaving(true);
    try {
      const supabase = createClient();
      const { error } = await supabase.auth.updateUser({ password: next });
      if (error) {
        toast.error(error.message);
        return;
      }
      toast.success("Password changed.");
      setNext("");
      setConfirm("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Change password" description="Update your account password.">
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-1.5">
          <Label htmlFor="pw-new">New password</Label>
          <Input
            id="pw-new"
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
            disabled={saving}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="pw-confirm">Confirm new password</Label>
          <Input
            id="pw-confirm"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            required
            autoComplete="new-password"
            disabled={saving}
          />
        </div>
        <div className="flex justify-end">
          <Button type="submit" disabled={saving || !next || !confirm}>
            {saving && <Loader2 className="animate-spin" />}
            Change password
          </Button>
        </div>
      </form>
    </Card>
  );
}

// ── Persona (Agent2 Day 2 #37 + Feature 1.11) ───────────────────────────────
// Function-style overlay applied to the system prompt for every chat turn.
// Three flavors:
//   • Built-in: one of 6 hardcoded function profiles.
//   • Custom:   user-authored prompt saved on the user row.
//   • Org:      admin-curated shared persona ("org:<uuid>") — see PersonaCard
//                 reads from /api/org-personas.

const BUILTIN_PERSONA_OPTIONS: ReadonlyArray<{
  value: import("@/hooks/use-user").BuiltInPersona;
  label: string;
  description: string;
}> = [
  { value: "hr", label: "HR", description: "Bias toward policy + compliance docs. Formal tone." },
  { value: "sales", label: "Sales", description: "Bias toward pricing + case studies. Persuasive framing." },
  { value: "engineering", label: "Engineering", description: "Bias toward specs + runbooks. Precise + technical." },
  { value: "finance", label: "Finance", description: "Bias toward budget docs. Specific figures + citations." },
  { value: "operations", label: "Operations", description: "Bias toward SOPs. Step-by-step procedures." },
  { value: "executive", label: "Executive", description: "Cross-collection synthesis. Strategic framing." },
];

function PersonaCard({
  persona,
  customName,
  customInstructions,
  onSaved,
}: {
  persona: string | null;
  customName: string | null;
  customInstructions: string | null;
  onSaved: () => void;
}) {
  const { personas: orgPersonas } = useOrgPersonas();
  const [value, setValue] = useState<string>(persona ?? "");
  const [saving, setSaving] = useState(false);

  // Custom prompt editing state — only used when value === "custom".
  const [draftName, setDraftName] = useState<string>(customName ?? "");
  const [draftInstr, setDraftInstr] = useState<string>(customInstructions ?? "");

  useEffect(() => {
    setValue(persona ?? "");
  }, [persona]);
  useEffect(() => {
    setDraftName(customName ?? "");
    setDraftInstr(customInstructions ?? "");
  }, [customName, customInstructions]);

  const persist = async (
    body: Record<string, unknown>,
    successMessage: string,
  ): Promise<boolean> => {
    setSaving(true);
    try {
      const res = await fetch("/api/users/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).catch((err) => {
        throw networkError(err);
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success(successMessage);
      onSaved();
      return true;
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const onSelectChange = async (next: string) => {
    const prev = value;
    setValue(next);
    if (!next) {
      const ok = await persist({ clear_persona: true }, "Persona cleared.");
      if (!ok) setValue(prev);
      return;
    }
    if (next === "custom") {
      // Don't save until the user fills in instructions.
      return;
    }
    const ok = await persist({ persona: next }, "Persona updated.");
    if (!ok) setValue(prev);
  };

  const saveCustom = async () => {
    const name = draftName.trim();
    const instr = draftInstr.trim();
    if (instr.length < 10) {
      toast.error("Add at least 10 characters of instructions.");
      return;
    }
    await persist(
      {
        persona: "custom",
        custom_persona_name: name || null,
        custom_persona_instructions: instr,
      },
      "Custom persona saved.",
    );
  };

  // Resolve the description for the currently-selected persona (built-in or org).
  const selectedDescription = (() => {
    if (!value) return null;
    const builtin = BUILTIN_PERSONA_OPTIONS.find((o) => o.value === value);
    if (builtin) return builtin.description;
    if (value === "custom") {
      return customInstructions
        ? `Your custom persona${customName ? ` — "${customName}"` : ""}.`
        : "Add a name + instructions below.";
    }
    if (value.startsWith("org:")) {
      const id = value.slice(4);
      const op = orgPersonas.find((p) => p.id === id);
      return op?.description ?? op?.instructions?.slice(0, 140) ?? null;
    }
    return null;
  })();

  return (
    <Card
      title="Chat persona"
      description="Tailors answer framing to your function. Doesn't change which documents are searched — your conversation scope does that."
    >
      <div className="space-y-3">
        <select
          value={value}
          disabled={saving}
          onChange={(e) => void onSelectChange(e.target.value)}
          className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          aria-label="Chat persona"
        >
          <option value="">No persona (default)</option>
          <optgroup label="Built-in">
            {BUILTIN_PERSONA_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </optgroup>
          {orgPersonas.length > 0 && (
            <optgroup label="Shared in your org">
              {orgPersonas.map((p) => (
                <option key={p.id} value={`org:${p.id}`}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Personal">
            <option value="custom">Custom (your own prompt)…</option>
          </optgroup>
        </select>
        {selectedDescription && (
          <p className="text-xs text-muted-foreground">{selectedDescription}</p>
        )}

        {value === "custom" && (
          <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3">
            <Label htmlFor="custom-persona-name" className="text-xs">
              Role name <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Input
              id="custom-persona-name"
              value={draftName}
              maxLength={80}
              placeholder="e.g. Customer Success Manager"
              onChange={(e) => setDraftName(e.target.value)}
              disabled={saving}
            />
            <Label htmlFor="custom-persona-instr" className="text-xs">
              Instructions{" "}
              <span className="text-muted-foreground">
                ({draftInstr.length}/2000)
              </span>
            </Label>
            <Textarea
              id="custom-persona-instr"
              value={draftInstr}
              maxLength={2000}
              rows={4}
              placeholder="Bias retrieval toward… Format answers as… Use a tone that…"
              onChange={(e) => setDraftInstr(e.target.value)}
              disabled={saving}
            />
            <div className="flex justify-end">
              <Button
                size="sm"
                onClick={() => void saveCustom()}
                disabled={saving || draftInstr.trim().length < 10}
              >
                {saving ? "Saving…" : "Save persona"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

// ── Briefing preferences (Feature 2.2) ──────────────────────────────────────
// Per-user opt-in + schedule for the Monday-morning briefing.

interface BriefingPrefs {
  enabled: boolean;
  weekday: number;
  hour: number;
  timezone: string;
  via_email: boolean;
  via_inapp: boolean;
  topics: string[];
}

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function BriefingPreferencesCard() {
  const { data, error, isLoading, mutate } = useSWR<{ preferences: BriefingPrefs }>(
    "/api/briefings/preferences",
    async (url) => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Failed to load (${res.status})`);
      return res.json();
    },
    { revalidateOnFocus: false },
  );
  const [saving, setSaving] = useState(false);

  const save = async (patch: Partial<BriefingPrefs>) => {
    setSaving(true);
    try {
      const res = await fetch("/api/briefings/preferences", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const errBody = (await res.json().catch(() => ({}))) as { message?: string };
        toast.error(errBody.message ?? "Could not save.");
        return;
      }
      toast.success("Briefing settings saved.");
      await mutate();
    } finally {
      setSaving(false);
    }
  };

  const runNow = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/briefings/run-now", { method: "POST" });
      if (!res.ok) {
        toast.error("Could not queue a briefing.");
        return;
      }
      toast.success("Briefing queued. It'll appear in your bell within a minute or two.");
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return (
      <Card
        title="Weekly briefing"
        description="A Monday-morning snapshot of what needs your attention."
      >
        <p className="text-sm text-muted-foreground">
          Couldn&apos;t load your preferences. Refresh to retry.
        </p>
      </Card>
    );
  }

  if (isLoading || !data) {
    return (
      <Card
        title="Weekly briefing"
        description="A Monday-morning snapshot of what needs your attention."
      >
        <div className="space-y-2">
          <div className="h-8 w-full rounded bg-muted" />
          <div className="h-8 w-2/3 rounded bg-muted" />
        </div>
      </Card>
    );
  }

  const prefs = data.preferences;
  const browserTz = typeof Intl !== "undefined"
    ? Intl.DateTimeFormat().resolvedOptions().timeZone
    : "UTC";

  return (
    <Card
      title="Weekly briefing"
      description="A Monday-morning snapshot of what needs your attention. Synthesizes your meetings, knowledge gaps, and stale docs into one short read."
    >
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium">Send briefings</p>
            <p className="text-xs text-muted-foreground">
              Toggles delivery without losing your schedule.
            </p>
          </div>
          <Button
            size="sm"
            variant={prefs.enabled ? "primary" : "outline"}
            disabled={saving}
            onClick={() => void save({ enabled: !prefs.enabled })}
          >
            {prefs.enabled ? "On" : "Off"}
          </Button>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <Label className="text-xs">Day</Label>
            <select
              value={prefs.weekday}
              disabled={saving || !prefs.enabled}
              onChange={(e) => void save({ weekday: Number(e.target.value) })}
              className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              {WEEKDAYS.map((label, idx) => (
                <option key={idx} value={idx}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Hour (local)</Label>
            <select
              value={prefs.hour}
              disabled={saving || !prefs.enabled}
              onChange={(e) => void save({ hour: Number(e.target.value) })}
              className="block w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>
                  {i.toString().padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Timezone</Label>
            <div className="flex items-center gap-2">
              <Input
                value={prefs.timezone}
                disabled={saving || !prefs.enabled}
                onChange={(e) =>
                  void mutate(
                    { preferences: { ...prefs, timezone: e.target.value } },
                    { revalidate: false },
                  )
                }
                onBlur={(e) =>
                  e.target.value !== prefs.timezone &&
                  void save({ timezone: e.target.value || "UTC" })
                }
              />
              {browserTz && browserTz !== prefs.timezone && (
                <Button
                  size="sm"
                  variant="ghost"
                  type="button"
                  disabled={saving}
                  onClick={() => void save({ timezone: browserTz })}
                >
                  Use {browserTz}
                </Button>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={prefs.via_email ? "primary" : "outline"}
            disabled={saving || !prefs.enabled}
            onClick={() => void save({ via_email: !prefs.via_email })}
          >
            <Mail className="mr-1.5 h-3.5 w-3.5" />
            Email {prefs.via_email ? "on" : "off"}
          </Button>
          <Button
            size="sm"
            variant={prefs.via_inapp ? "primary" : "outline"}
            disabled={saving || !prefs.enabled}
            onClick={() => void save({ via_inapp: !prefs.via_inapp })}
          >
            In-app {prefs.via_inapp ? "on" : "off"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={saving || !prefs.enabled}
            onClick={() => void runNow()}
          >
            Send a preview now
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ── Activity privacy (V4 #57) ───────────────────────────────────────────────

function ActivityPrivacyCard({
  activityPrivate,
  onSaved,
}: {
  activityPrivate: boolean;
  onSaved: () => void;
}) {
  const [value, setValue] = useState(activityPrivate);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(activityPrivate);
  }, [activityPrivate]);

  const dirty = value !== activityPrivate;

  const save = async (next: boolean) => {
    setValue(next);
    setSaving(true);
    try {
      const res = await fetch("/api/users/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activity_private: next }),
      }).catch((err) => {
        throw networkError(err);
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success(
        next
          ? "New activity will stay private."
          : "New activity will show in the team feed.",
      );
      onSaved();
    } catch (err) {
      setValue(!next);
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="Activity privacy"
      description="Control whether teammates see your activity in the sidebar feed."
    >
      <label className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">Hide my activity</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Only affects new activity. Past entries keep their visibility.
          </p>
        </div>
        <input
          type="checkbox"
          className="mt-1 h-4 w-7 cursor-pointer appearance-none rounded-full bg-muted transition-colors checked:bg-primary disabled:opacity-50 relative before:absolute before:top-0.5 before:left-0.5 before:h-3 before:w-3 before:rounded-full before:bg-background before:transition-transform checked:before:translate-x-3"
          checked={value}
          disabled={saving}
          onChange={(e) => save(e.target.checked)}
          aria-label="Hide my activity"
        />
      </label>
      {dirty && saving ? (
        <p className="text-xs text-muted-foreground">
          <Loader2 className="mr-1 inline h-3 w-3 animate-spin" />
          Saving…
        </p>
      ) : null}
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

function TeamCard({
  isAdmin,
  currentUserId,
}: {
  isAdmin: boolean;
  currentUserId: string | null;
}) {
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
  const [roleTitle, setRoleTitle] = useState("");
  const [sending, setSending] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [roleBusy, setRoleBusy] = useState<string | null>(null);
  const [lastInvite, setLastInvite] = useState<{
    email: string;
    url: string;
  } | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);

  const sendInvite = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed)) {
      toast.error("Enter a valid email address.");
      return;
    }
    const trimmedRoleTitle = roleTitle.trim();
    if (!trimmedRoleTitle) {
      toast.error("Role title is required.");
      return;
    }
    setSending(true);
    try {
      const res = await fetch("/api/organizations/invitations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: trimmed,
          role,
          role_title: trimmedRoleTitle,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        message?: string;
        detail?: string;
        invitation?: { accept_url?: string };
      };
      if (!res.ok) {
        toast.error(data.message ?? data.detail ?? "Failed to send invite.");
        return;
      }
      toast.success(`Invite sent to ${trimmed}.`);
      const acceptUrl = data.invitation?.accept_url;
      if (acceptUrl) {
        setLastInvite({ email: trimmed, url: acceptUrl });
        setLinkCopied(false);
      }
      setEmail("");
      setRole("member");
      setRoleTitle("");
      invites.mutate();
    } finally {
      setSending(false);
    }
  };

  const copyInviteLink = async () => {
    if (!lastInvite) return;
    try {
      await navigator.clipboard.writeText(lastInvite.url);
      setLinkCopied(true);
      toast.success("Invite link copied.");
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      toast.error("Couldn't copy. Select the link and copy it manually.");
    }
  };

  const confirmRemoveMember = async () => {
    if (!removing) return;
    setRemoveBusy(true);
    try {
      const res = await fetch(
        `/api/organizations/members/${removing.id}`,
        { method: "DELETE" },
      );
      if (!res.ok && res.status !== 204) {
        const data = (await res.json().catch(() => ({}))) as {
          message?: string;
          detail?: string;
        };
        toast.error(data.message ?? data.detail ?? "Failed to remove member.");
        return;
      }
      toast.success(
        `Removed ${removing.display_name ?? removing.email ?? "member"} from the workspace.`,
      );
      setRemoving(null);
      members.mutate();
    } finally {
      setRemoveBusy(false);
    }
  };

  const changeRole = async (
    member: Member,
    newRole: "admin" | "member",
  ) => {
    setRoleBusy(member.id);
    try {
      const res = await fetch(
        `/api/organizations/members/${member.id}/role`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: newRole }),
        },
      );
      const data = (await res.json().catch(() => ({}))) as {
        message?: string;
        detail?: string;
      };
      if (!res.ok) {
        toast.error(data.message ?? data.detail ?? "Failed to update role.");
        return;
      }
      toast.success(
        `${member.display_name ?? member.email ?? "Member"} is now ${newRole === "admin" ? "an admin" : "a member"}.`,
      );
      members.mutate();
    } catch {
      toast.error("Network error. Try again.");
    } finally {
      setRoleBusy(null);
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
            {(members.data?.members ?? []).map((m) => {
              const isSelf = m.id === currentUserId;
              const canRemove = isAdmin && !isSelf;
              return (
                <li
                  key={m.id}
                  className="flex items-center justify-between gap-3 px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      {m.display_name ?? "—"}
                      {isSelf && (
                        <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                          (you)
                        </span>
                      )}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {m.email ?? "unknown"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <RoleBadge role={m.role} />
                    {canRemove && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            aria-label={`Manage ${m.display_name ?? m.email ?? "member"}`}
                            disabled={roleBusy === m.id}
                            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                          >
                            {roleBusy === m.id ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <MoreHorizontal className="h-3.5 w-3.5" />
                            )}
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-48">
                          {m.role === "member" ? (
                            <DropdownMenuItem
                              onSelect={() => changeRole(m, "admin")}
                            >
                              <ShieldCheck className="h-3.5 w-3.5" />
                              Make admin
                            </DropdownMenuItem>
                          ) : (
                            <DropdownMenuItem
                              onSelect={() => changeRole(m, "member")}
                            >
                              <UserCheck className="h-3.5 w-3.5" />
                              Make member
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            destructive
                            onSelect={() => setRemoving(m)}
                          >
                            <UserMinus className="h-3.5 w-3.5" />
                            Remove from workspace
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </div>
                </li>
              );
            })}
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

      <AlertDialog
        open={!!removing}
        onOpenChange={(o) => !o && !removeBusy && setRemoving(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this member?</AlertDialogTitle>
            <AlertDialogDescription>
              <strong className="font-medium text-foreground">
                {removing?.display_name ?? removing?.email ?? "This person"}
              </strong>{" "}
              will lose access to the workspace immediately. Their conversations
              will be deleted; documents they uploaded stay in the workspace.
              You can re-invite them later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removeBusy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                confirmRemoveMember();
              }}
              disabled={removeBusy}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {removeBusy && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
              Remove member
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isAdmin && (
        <div className="space-y-4 rounded-md border border-dashed border-border bg-muted/30 p-4">
          <div className="flex items-center gap-1.5 text-xs font-medium">
            <Mail className="h-3.5 w-3.5" />
            Invite by email
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_140px]">
            <div className="space-y-1.5">
              <Label htmlFor="invite-email" className="text-[11px]">
                Email <span className="text-destructive">*</span>
              </Label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="teammate@company.com"
                disabled={sending}
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-role" className="text-[11px]">
                Role
              </Label>
              <select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value as "admin" | "member")}
                disabled={sending}
                className="block h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="invite-role-title" className="text-[11px]">
              Role title <span className="text-destructive">*</span>
            </Label>
            <Input
              id="invite-role-title"
              value={roleTitle}
              onChange={(e) => setRoleTitle(e.target.value)}
              placeholder="Senior Engineer"
              disabled={sending}
              required
            />
            <p className="text-[11px] text-muted-foreground">
              Used by the onboarding agent to personalise the welcome email.
            </p>
          </div>

          <div className="flex justify-end">
            <Button
              onClick={sendInvite}
              disabled={sending || !email || !roleTitle.trim()}
            >
              {sending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <UserPlus className="h-3.5 w-3.5" />
              )}
              Send invite
            </Button>
          </div>

          {lastInvite && (
            <div className="space-y-1.5 rounded-md border border-border bg-background p-3">
              <div className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
                <Link2 className="h-3.5 w-3.5" />
                Invite link for {lastInvite.email}
              </div>
              <div className="flex items-center gap-2">
                <Input
                  readOnly
                  value={lastInvite.url}
                  onFocus={(e) => e.currentTarget.select()}
                  className="h-8 flex-1 font-mono text-xs"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={copyInviteLink}
                  className="shrink-0"
                >
                  {linkCopied ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  {linkCopied ? "Copied" : "Copy"}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                We emailed this link too — copy it to share directly (e.g. via
                Slack or DM). It expires in 15 days.
              </p>
            </div>
          )}
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
            <Skeleton className="h-3 w-32" />
            <Skeleton className="h-2 w-40" />
          </div>
          <Skeleton className="h-4 w-12" />
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
  if (res.status === 401) {
    if (typeof window !== "undefined") {
      const next = encodeURIComponent(
        window.location.pathname + window.location.search,
      );
      window.location.href = `/login?redirectedFrom=${next}`;
    }
    throw new Error("Unauthorized");
  }
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
            icon={<ThumbsUp className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />}
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

// ── AI Instructions (Day 9 / #67) ───────────────────────────────────────────

const AI_INSTRUCTIONS_MAX = 500;

function AIInstructionsCard({ canEdit }: { canEdit: boolean }) {
  const { data, error, isLoading, mutate } = useSWR<{ ai_instructions: string }>(
    "/api/organizations/me/ai-settings",
    jsonFetcher,
    { revalidateOnFocus: false },
  );

  const initial = data?.ai_instructions ?? "";
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValue(initial);
  }, [initial]);

  const trimmed = value.trim();
  const dirty = trimmed !== initial.trim();
  const overLimit = trimmed.length > AI_INSTRUCTIONS_MAX;

  const save = async () => {
    if (overLimit) return;
    setSaving(true);
    try {
      const res = await fetch("/api/organizations/me/ai-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ai_instructions: trimmed || null }),
      }).catch((err) => {
        throw networkError(err);
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success(
        trimmed ? "AI instructions saved." : "AI instructions cleared.",
      );
      await mutate();
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    setValue("");
    if (!initial) return;
    setSaving(true);
    try {
      const res = await fetch("/api/organizations/me/ai-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ai_instructions: null }),
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success("AI instructions cleared.");
      await mutate();
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="AI instructions"
      description={
        canEdit
          ? "Use it to describe your company, set tone guidelines, or specify things the AI should never mention."
          : "Org-wide context the AI uses on every response. Only workspace admins can change this."
      }
    >
      {isLoading ? (
        <Skeleton className="h-24" />
      ) : error ? (
        <p className="text-sm text-destructive">
          Couldn&apos;t load AI instructions. Try again later.
        </p>
      ) : (
        <>
          <div className="grid gap-1.5">
            <Textarea
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="We're a B2B SaaS for accounting firms. Tone is formal but approachable. Never mention competitor pricing."
              rows={5}
              maxLength={AI_INSTRUCTIONS_MAX + 50}
              disabled={!canEdit || saving}
            />
            <div className="flex items-center justify-between text-[11px]">
              <p className="text-muted-foreground">
                Leave empty to clear. Applies to every chat in this workspace.
              </p>
              <p
                className={
                  overLimit
                    ? "font-medium text-destructive tabular-nums"
                    : "text-muted-foreground tabular-nums"
                }
              >
                {trimmed.length}/{AI_INSTRUCTIONS_MAX}
              </p>
            </div>
          </div>

          <div className="flex justify-end gap-2">
            {initial && canEdit && (
              <Button variant="ghost" onClick={clear} disabled={saving}>
                Clear
              </Button>
            )}
            <Button
              onClick={save}
              disabled={!canEdit || !dirty || saving || overLimit}
            >
              {saving && <Loader2 className="animate-spin" />}
              Save instructions
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}


// ── Competitor watchlist ────────────────────────────────────────────────────
//
// Two layers, surfaced as sub-sections of a single card so the user
// understands the org list is the policy floor and the personal list is an
// optional overlay. Both lists save independently.
//
// Editor pattern: chip input. Type a name, press Enter / comma / Tab to
// commit, click X on a chip to remove. Save button is per-section and
// only enabled when the list differs from the server snapshot.

interface CompetitorListResponse {
  names: string[];
  max: number;
}

function CompetitorWatchlistCard({
  canEditOrg,
}: {
  canEditOrg: boolean;
}) {
  return (
    <Card
      title="Competitor watch"
      description="Flag when AI outputs mention specific companies. We highlight matches inline, ask you to confirm before exporting flagged content, and surface a review queue for admins."
    >
      <div className="space-y-6">
        <CompetitorListEditor
          endpoint="/api/organizations/me/competitors"
          label="Workspace list"
          help={
            canEditOrg
              ? "Applied to every chat and agent output in this workspace. Whole-word, case-insensitive."
              : "Workspace-wide list managed by your admins. Visible so you know what's being watched."
          }
          canEdit={canEditOrg}
          icon={<ShieldAlert className="h-4 w-4 text-amber-600" />}
        />
        <div className="border-t border-border" />
        <CompetitorListEditor
          endpoint="/api/users/me/competitors"
          label="Your personal watchlist"
          help="Extra terms layered on top of the workspace list — only your own outputs are scanned."
          canEdit={true}
          icon={null}
        />
      </div>
    </Card>
  );
}

function CompetitorListEditor({
  endpoint,
  label,
  help,
  canEdit,
  icon,
}: {
  endpoint: string;
  label: string;
  help: string;
  canEdit: boolean;
  icon: React.ReactNode;
}) {
  const { data, error, isLoading, mutate } = useSWR<CompetitorListResponse>(
    endpoint,
    jsonFetcher,
    { revalidateOnFocus: false },
  );

  const initial = data?.names ?? [];
  const max = data?.max ?? 100;

  const [names, setNames] = useState<string[]>(initial);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  // Sync local state when server-side list arrives or refreshes.
  useEffect(() => {
    setNames(initial);
    // Intentionally use the joined snapshot as the dep so a refetch with the
    // same array contents doesn't churn local state mid-edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial.join("")]);

  const dirty =
    names.length !== initial.length ||
    names.some((n, i) => n !== initial[i]);

  const commitDraft = () => {
    const cleaned = draft.trim();
    if (!cleaned) {
      setDraft("");
      return;
    }
    if (cleaned.length > 200) {
      toast.error("Competitor name is too long (max 200 chars).");
      return;
    }
    if (names.length >= max) {
      toast.error(`Limit reached — at most ${max} names.`);
      return;
    }
    if (names.some((n) => n.toLowerCase() === cleaned.toLowerCase())) {
      setDraft("");
      return;
    }
    setNames([...names, cleaned]);
    setDraft("");
  };

  const removeAt = (idx: number) => {
    setNames(names.filter((_, i) => i !== idx));
  };

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
      });
      if (!res.ok) throw await parseApiError(res);
      const next = (await res.json()) as CompetitorListResponse;
      await mutate(next, { revalidate: false });
      toast.success("Watchlist updated.");
    } catch (err) {
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === "," || e.key === "Tab") {
      if (draft.trim()) {
        e.preventDefault();
        commitDraft();
      }
      return;
    }
    if (e.key === "Backspace" && !draft && names.length > 0) {
      // Quick undo of the most recent chip without aiming for it.
      e.preventDefault();
      setNames(names.slice(0, -1));
    }
  };

  if (isLoading) {
    return <Skeleton className="h-24" />;
  }
  if (error || !data) {
    return (
      <p className="text-sm text-destructive">
        Couldn&apos;t load the watchlist. Try again later.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        {icon}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{label}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{help}</p>
        </div>
      </div>

      <div
        className={`flex flex-wrap items-center gap-1.5 rounded-md border border-input bg-background p-2 text-sm ${
          canEdit ? "" : "opacity-70"
        }`}
      >
        {names.map((name, idx) => (
          <span
            key={`${name}-${idx}`}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs"
          >
            {name}
            {canEdit && (
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="rounded-full p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                aria-label={`Remove ${name}`}
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        ))}
        {canEdit && (
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={() => draft.trim() && commitDraft()}
            placeholder={
              names.length === 0
                ? "Type a name and press Enter…"
                : "Add another…"
            }
            disabled={saving || names.length >= max}
            className="flex-1 min-w-[10rem] bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            maxLength={200}
          />
        )}
      </div>

      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {names.length}/{max}
        </span>
        {canEdit && (
          <Button
            onClick={save}
            disabled={!dirty || saving}
            size="sm"
            variant={dirty ? "primary" : "ghost"}
          >
            {saving && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            Save
          </Button>
        )}
      </div>
    </div>
  );
}


// ── Sharing toggle (V3 Day 4 #62) ──────────────────────────────────────────

function SharingCard({ canEdit }: { canEdit: boolean }) {
  const { data, error, isLoading, mutate } = useSWR<{
    allow_output_sharing: boolean;
  }>("/api/organizations/me/sharing", jsonFetcher, {
    revalidateOnFocus: false,
  });

  const [busy, setBusy] = useState(false);
  const allowed = !!data?.allow_output_sharing;

  const toggle = async (next: boolean) => {
    setBusy(true);
    // Optimistic — flip immediately, revert on failure.
    await mutate({ allow_output_sharing: next }, { revalidate: false });
    try {
      const res = await fetch("/api/organizations/me/sharing", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow_output_sharing: next }),
      });
      if (!res.ok) throw await parseApiError(res);
      toast.success(
        next
          ? "Shared links enabled for the workspace."
          : "Shared links disabled. Existing links will stop resolving.",
      );
      await mutate();
    } catch (err) {
      await mutate({ allow_output_sharing: !next }, { revalidate: false });
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Shared links"
      description={
        canEdit
          ? "Let teammates publish public links to AI outputs. Existing links stop working when this is off."
          : "Whether your team can publish public links to AI outputs. Only admins can change this."
      }
    >
      {isLoading ? (
        <Skeleton className="h-10" />
      ) : error ? (
        <p className="text-sm text-destructive">
          Couldn&apos;t load sharing settings. Try again later.
        </p>
      ) : (
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">
              Allow public output sharing
            </p>
            <p className="text-xs text-muted-foreground">
              Each share is opt-in per message. Anyone with a link can read it
              without signing in.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={allowed}
            disabled={!canEdit || busy}
            onClick={() => toggle(!allowed)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              allowed ? "bg-primary" : "bg-muted"
            }`}
          >
            <span
              aria-hidden
              className={`inline-block h-5 w-5 transform rounded-full bg-background shadow ring-0 transition-transform ${
                allowed ? "translate-x-5" : "translate-x-0"
              }`}
            />
          </button>
        </div>
      )}
    </Card>
  );
}


// ── Conversation archive (V3 #104) ──────────────────────────────────────────

interface ArchiveSettings {
  auto_archive_enabled: boolean;
  threshold_days: number;
  delete_after_archive_days: number | null;
  allowed_threshold_days: number[];
}

function ArchiveCard({ canEdit }: { canEdit: boolean }) {
  const { data, error, isLoading, mutate } = useSWR<ArchiveSettings>(
    "/api/organizations/me/archive-settings",
    jsonFetcher,
    { revalidateOnFocus: false },
  );
  const [busy, setBusy] = useState(false);

  const patch = async (
    body: {
      auto_archive_enabled?: boolean;
      threshold_days?: number;
      delete_after_archive_days?: number | null;
      clear_delete_retention?: boolean;
    },
    optimistic?: Partial<ArchiveSettings>,
  ) => {
    if (!data) return;
    const previous = data;
    if (optimistic) {
      await mutate({ ...data, ...optimistic } as ArchiveSettings, {
        revalidate: false,
      });
    }
    setBusy(true);
    try {
      const res = await fetch("/api/organizations/me/archive-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw await parseApiError(res);
      const next = (await res.json()) as ArchiveSettings;
      await mutate(next, { revalidate: false });
      toast.success("Archive settings updated.");
    } catch (err) {
      await mutate(previous, { revalidate: false });
      reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Conversation archive"
      description={
        canEdit
          ? "Auto-archive idle conversations to keep the sidebar tidy. Pinned conversations are never auto-archived."
          : "How conversations are auto-archived for your workspace. Only admins can change these settings."
      }
    >
      {isLoading ? (
        <Skeleton className="h-24" />
      ) : error || !data ? (
        <p className="text-sm text-destructive">
          Couldn&apos;t load archive settings. Try again later.
        </p>
      ) : (
        <div className="space-y-5">
          {/* Auto-archive toggle */}
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">
                Auto-archive idle conversations
              </p>
              <p className="text-xs text-muted-foreground">
                Hides conversations from the sidebar after a period of
                inactivity. They stay searchable and can be restored.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={data.auto_archive_enabled}
              disabled={!canEdit || busy}
              onClick={() =>
                patch(
                  { auto_archive_enabled: !data.auto_archive_enabled },
                  { auto_archive_enabled: !data.auto_archive_enabled },
                )
              }
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                data.auto_archive_enabled ? "bg-primary" : "bg-muted"
              }`}
            >
              <span
                aria-hidden
                className={`inline-block h-5 w-5 transform rounded-full bg-background shadow ring-0 transition-transform ${
                  data.auto_archive_enabled ? "translate-x-5" : "translate-x-0"
                }`}
              />
            </button>
          </div>

          {/* Threshold select */}
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <Label
                htmlFor="archive-threshold"
                className="text-sm font-medium text-foreground"
              >
                Archive after
              </Label>
              <p className="text-xs text-muted-foreground">
                Time without any read or write activity before a conversation is
                archived.
              </p>
            </div>
            <select
              id="archive-threshold"
              value={data.threshold_days}
              disabled={!canEdit || busy || !data.auto_archive_enabled}
              onChange={(e) =>
                patch(
                  { threshold_days: Number(e.target.value) },
                  { threshold_days: Number(e.target.value) },
                )
              }
              className="h-9 rounded-md border border-border bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
              {(data.allowed_threshold_days ?? [30, 45, 60, 90, 180]).map(
                (d) => (
                  <option key={d} value={d}>
                    {d} days
                  </option>
                ),
              )}
            </select>
          </div>

          {/* Delete-after-archive (retention tier) */}
          <div className="flex flex-col gap-2 rounded-md border border-dashed border-border bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">
                Permanently delete after archive
              </p>
              <p className="text-xs text-muted-foreground">
                Optional retention window. Archived conversations beyond this
                age are deleted forever. Off by default.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <select
                value={data.delete_after_archive_days ?? "off"}
                disabled={!canEdit || busy}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value === "off") {
                    patch(
                      { clear_delete_retention: true },
                      { delete_after_archive_days: null },
                    );
                  } else {
                    const n = Number(value);
                    patch(
                      { delete_after_archive_days: n },
                      { delete_after_archive_days: n },
                    );
                  }
                }}
                className="h-9 rounded-md border border-border bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              >
                <option value="off">Never</option>
                <option value="30">30 days</option>
                <option value="60">60 days</option>
                <option value="90">90 days</option>
                <option value="180">180 days</option>
                <option value="365">365 days</option>
              </select>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}


// ── GDPR data export ────────────────────────────────────────────────────────
//
// Two buttons coexist here: a personal "Download my data" available to every
// user, and an admin-only "Export workspace data" sitting below it. We keep
// them in one card rather than two so the user understands the relationship
// — admins can grab either, members only their own.

function DataExportCard({ isAdmin }: { isAdmin: boolean }) {
  const [busy, setBusy] = useState<"user" | "org" | null>(null);

  const triggerDownload = async (
    kind: "user" | "org",
    endpoint: string,
    failureMessage: string,
  ) => {
    if (busy) return;
    setBusy(kind);
    try {
      const res = await fetch(endpoint, { method: "GET" });
      if (res.status === 429) {
        const data = (await res.json().catch(() => ({}))) as {
          message?: string;
          detail?: string;
        };
        toast.error(
          data.message ?? data.detail ?? "Export limit reached. Try again later.",
        );
        return;
      }
      if (!res.ok) {
        throw await parseApiError(res);
      }
      // Mint a download from the blob so we can carry the auth-aware fetch
      // pipeline rather than relying on cookie-credentialed <a download>
      // (the upstream API uses Bearer tokens, not cookies).
      const blob = await res.blob();
      const disposition = res.headers.get("content-disposition") ?? "";
      const match = /filename="?([^";]+)"?/.exec(disposition);
      const filename = match?.[1] ?? `nirnayaiq-${kind}-export.zip`;

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Defer revocation so Safari doesn't cancel the in-flight save dialog.
      window.setTimeout(() => URL.revokeObjectURL(url), 5_000);
      toast.success(
        kind === "user"
          ? "Your data export is downloading."
          : "Workspace export is downloading.",
      );
    } catch (err) {
      if ((err as { message?: string })?.message) {
        reportApiError(err as Awaited<ReturnType<typeof parseApiError>>);
      } else {
        toast.error(failureMessage);
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      title="Your data"
      description="Download an archive of the personal data we hold about you, under GDPR Article 15 and analogous rights elsewhere."
    >
      <div className="rounded-md border border-border bg-muted/30 p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              Download my data
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              ZIP archive with your profile, conversations, messages, query
              history, document metadata, and integration connections (without
              tokens). Limited to 3 exports per day.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() =>
              triggerDownload(
                "user",
                "/api/users/me/export",
                "Couldn't build your data export. Try again in a moment.",
              )
            }
            disabled={busy !== null}
            className="self-start shrink-0 sm:self-auto"
          >
            {busy === "user" ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            Download (.zip)
          </Button>
        </div>
      </div>

      {isAdmin && (
        <div className="rounded-md border border-border bg-muted/30 p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <Building2 className="h-3.5 w-3.5 text-muted-foreground" />
                <p className="text-sm font-medium text-foreground">
                  Export workspace data
                </p>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Admin-only. Includes all workspace members, conversations,
                documents, and integration metadata — no OAuth tokens, no
                Stripe IDs, no document file contents. Limited to 1 export
                per day per workspace.
              </p>
            </div>
            <Button
              variant="outline"
              onClick={() =>
                triggerDownload(
                  "org",
                  "/api/organizations/me/export",
                  "Couldn't build the workspace export. Try again in a moment.",
                )
              }
              disabled={busy !== null}
              className="self-start shrink-0 sm:self-auto"
            >
              {busy === "org" ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              Download workspace (.zip)
            </Button>
          </div>
        </div>
      )}
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
      <div className="flex flex-col gap-3 rounded-md border border-destructive/20 bg-destructive-soft/60 p-4 sm:flex-row sm:items-start sm:justify-between">
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
