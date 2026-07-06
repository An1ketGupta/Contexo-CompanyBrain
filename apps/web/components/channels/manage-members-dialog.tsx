"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, UserPlus, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MemberPicker, type OrgMember } from "./member-picker";

type Role = "owner" | "editor" | "viewer";

const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner",
  editor: "Editor",
  viewer: "Viewer",
};

interface Participant {
  user_id: string;
  role: Role;
  users?: { display_name: string | null } | null;
}

export function ManageMembersDialog({
  channelId,
  channelTitle,
  myRole,
  open,
  onOpenChange,
  onChanged,
}: {
  channelId: string;
  channelTitle: string;
  myRole: Role | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged?: () => void;
}) {
  const [roster, setRoster] = useState<Participant[]>([]);
  const [directory, setDirectory] = useState<Record<string, OrgMember>>({});
  const [meId, setMeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [selected, setSelected] = useState<string[]>([]);
  const [inviteRole, setInviteRole] = useState<Role>("editor");
  const [inviting, setInviting] = useState(false);

  const isOwner = myRole === "owner";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [meRes, chRes, memRes] = await Promise.all([
        fetch("/api/me"),
        fetch(`/api/channels/${channelId}`),
        fetch("/api/organizations/members"),
      ]);
      const me = await meRes.json().catch(() => ({}));
      setMeId(me?.user?.id ?? null);

      const ch = await chRes.json().catch(() => ({}));
      setRoster((ch.participants as Participant[]) ?? []);

      const mem = await memRes.json().catch(() => ({}));
      const map: Record<string, OrgMember> = {};
      for (const m of (mem.members as OrgMember[]) ?? []) map[m.id] = m;
      setDirectory(map);
    } finally {
      setLoading(false);
    }
  }, [channelId]);

  useEffect(() => {
    if (!open) return;
    setSelected([]);
    setInviteRole("editor");
    void load();
  }, [open, load]);

  function nameFor(p: Participant): string {
    return (
      p.users?.display_name ||
      directory[p.user_id]?.display_name ||
      directory[p.user_id]?.email?.split("@")[0] ||
      "Someone"
    );
  }

  const existingIds = useMemo(() => roster.map((p) => p.user_id), [roster]);
  const ownerCount = useMemo(
    () => roster.filter((p) => p.role === "owner").length,
    [roster],
  );

  async function changeRole(userId: string, role: Role) {
    setBusyId(userId);
    try {
      const res = await fetch(
        `/api/channels/${channelId}/participants/${userId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          err.detail === "last_owner_cannot_demote"
            ? "This is the only owner — promote someone else first."
            : err.detail || `Couldn't update role (${res.status})`,
        );
      }
      setRoster((rows) =>
        rows.map((r) => (r.user_id === userId ? { ...r, role } : r)),
      );
      onChanged?.();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Couldn't update role.");
    } finally {
      setBusyId(null);
    }
  }

  async function removeMember(userId: string) {
    setBusyId(userId);
    try {
      const res = await fetch(
        `/api/channels/${channelId}/participants/${userId}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(
          err.detail === "last_owner_cannot_leave"
            ? "The only owner can't be removed — promote someone else first."
            : err.detail || `Couldn't remove (${res.status})`,
        );
      }
      setRoster((rows) => rows.filter((r) => r.user_id !== userId));
      onChanged?.();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Couldn't remove member.");
    } finally {
      setBusyId(null);
    }
  }

  async function invite() {
    if (selected.length === 0) return;
    setInviting(true);
    try {
      const res = await fetch(`/api/channels/${channelId}/invite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_ids: selected, role: inviteRole }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Invite failed (${res.status})`);
      }
      const data = await res.json();
      const count = data.count ?? selected.length;
      toast.success(
        count === 0
          ? "Everyone selected is already in this channel."
          : `Added ${count} ${count === 1 ? "person" : "people"}.`,
      );
      setSelected([]);
      await load();
      onChanged?.();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Couldn't add people.");
    } finally {
      setInviting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader className="min-w-0">
          <DialogTitle>Members</DialogTitle>
          <DialogDescription className="truncate">
            Manage who&apos;s in{" "}
            <span className="font-semibold text-body">{channelTitle}</span> and
            what they can do.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
          </div>
        ) : (
          <div className="min-w-0 space-y-5">
            <ul className="max-h-64 min-w-0 divide-y divide-border overflow-y-auto overflow-x-hidden rounded-xl border border-border">
              {roster.map((p) => {
                const isMe = p.user_id === meId;
                const isLastOwner = p.role === "owner" && ownerCount <= 1;
                const rowBusy = busyId === p.user_id;
                return (
                  <li
                    key={p.user_id}
                    className="flex min-w-0 items-center gap-3 px-3 py-2.5"
                  >
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted font-mono text-[11px] font-bold text-muted-foreground">
                      {nameFor(p).slice(0, 2).toUpperCase()}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-semibold text-foreground">
                          {nameFor(p)}
                        </span>
                        {isMe ? (
                          <span className="shrink-0 text-xs font-normal text-muted-foreground">
                            you
                          </span>
                        ) : null}
                      </span>
                      {directory[p.user_id]?.email ? (
                        <span className="block truncate text-xs text-muted-foreground">
                          {directory[p.user_id].email}
                        </span>
                      ) : null}
                    </span>

                    {isOwner && !isLastOwner ? (
                      <div className="flex shrink-0 items-center gap-1">
                        <Select
                          value={p.role}
                          onValueChange={(v) => changeRole(p.user_id, v as Role)}
                          disabled={rowBusy}
                        >
                          <SelectTrigger className="h-8 w-[104px] text-xs font-medium">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="owner">Owner</SelectItem>
                            <SelectItem value="editor">Editor</SelectItem>
                            <SelectItem value="viewer">Viewer</SelectItem>
                          </SelectContent>
                        </Select>
                        <button
                          type="button"
                          onClick={() => removeMember(p.user_id)}
                          disabled={rowBusy}
                          title={isMe ? "Leave channel" : "Remove"}
                          className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:pointer-events-none disabled:opacity-40"
                        >
                          {rowBusy ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <X className="size-4" />
                          )}
                        </button>
                      </div>
                    ) : (
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-wide",
                          p.role === "owner"
                            ? "bg-violet-tint text-violet"
                            : p.role === "editor"
                              ? "bg-brand-tint text-brand"
                              : "bg-muted text-muted-foreground",
                        )}
                        title={
                          isOwner && isLastOwner
                            ? "The only owner — promote someone else to change this"
                            : undefined
                        }
                      >
                        {ROLE_LABEL[p.role]}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>

            {isOwner ? (
              <div className="min-w-0 space-y-3 border-t border-border pt-4">
                <p className="font-mono text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                  Add people
                </p>
                <MemberPicker
                  selected={selected}
                  onChange={setSelected}
                  excludeIds={existingIds}
                />
                <div className="flex min-w-0 items-center gap-2">
                  <Select
                    value={inviteRole}
                    onValueChange={(v) => setInviteRole(v as Role)}
                  >
                    <SelectTrigger className="h-10 w-32 shrink-0">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="owner">Owner</SelectItem>
                      <SelectItem value="editor">Editor</SelectItem>
                      <SelectItem value="viewer">Viewer</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    className="min-w-0 flex-1"
                    onClick={invite}
                    disabled={inviting || selected.length === 0}
                  >
                    {inviting ? (
                      <Loader2 className="size-4 mr-2 animate-spin" />
                    ) : (
                      <UserPlus className="size-4 mr-2" />
                    )}
                    Add{selected.length > 0 ? ` ${selected.length}` : ""}
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
