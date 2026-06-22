"use client";

import { useState } from "react";
import Link from "next/link";
import {
  CalendarClock,
  ChevronLeft,
  Loader2,
  MoreHorizontal,
  Plus,
  Send,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { ScheduledReportDialog } from "@/components/settings/scheduled-report-dialog";
import {
  useScheduledReports,
  type ScheduledReport,
} from "@/hooks/use-scheduled-reports";
import { useCurrentUser } from "@/hooks/use-user";

const DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TYPE_LABEL: Record<ScheduledReport["report_type"], string> = {
  usage_summary: "Usage summary",
  knowledge_health: "Knowledge health",
};

function formatSchedule(r: ScheduledReport): string {
  const time = `${r.send_time_utc.toString().padStart(2, "0")}:00 UTC`;
  if (r.frequency === "daily") return `Daily at ${time}`;
  if (r.frequency === "weekly")
    return `Every ${DAYS_OF_WEEK[r.day_of_week ?? 0]} at ${time}`;
  return `Monthly on day ${r.day_of_month ?? 1} at ${time}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ScheduledReportsPage() {
  const { user } = useCurrentUser();
  const isAdmin = user?.role === "admin";
  const {
    reports,
    isLoading,
    error,
    updateReport,
    deleteReport,
    sendNow,
  } = useScheduledReports();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduledReport | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledReport | null>(null);
  const [sendingId, setSendingId] = useState<string | null>(null);

  async function toggleActive(r: ScheduledReport, next: boolean) {
    try {
      await updateReport(r.id, { is_active: next });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not update");
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await deleteReport(deleteTarget.id);
      toast.success("Report deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not delete");
    } finally {
      setDeleteTarget(null);
    }
  }

  async function handleSendNow(r: ScheduledReport) {
    setSendingId(r.id);
    try {
      await sendNow(r.id);
      toast.success("Report queued — check your inbox shortly");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not send");
    } finally {
      setSendingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <Link
        href="/settings"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft size={14} />
        Settings
      </Link>

      <header className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <CalendarClock size={18} className="text-muted-foreground" />
            <h1 className="text-xl font-semibold tracking-tight">Scheduled reports</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Automated usage + knowledge-health emails to anyone you choose.
          </p>
        </div>
        {isAdmin && (
          <Button
            onClick={() => {
              setEditing(null);
              setDialogOpen(true);
            }}
            size="sm"
          >
            <Plus size={14} className="mr-1" />
            New report
          </Button>
        )}
      </header>

      {!isAdmin && (
        <div className="rounded-xl border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          Only org admins can create or edit scheduled reports.
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          {error.message}
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {!isLoading && reports.length === 0 && (
        <div className="rounded-xl border border-dashed border-border p-8 text-center">
          <CalendarClock className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No scheduled reports yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Set up your first recurring report to land in your inbox automatically.
          </p>
          {isAdmin && (
            <Button
              size="sm"
              className="mt-4"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus size={14} className="mr-1" /> New report
            </Button>
          )}
        </div>
      )}

      <div className="space-y-3">
        {reports.map((r) => (
          <div
            key={r.id}
            className="flex items-center gap-3 rounded-xl border border-border bg-card p-4 shadow-sm"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium">{TYPE_LABEL[r.report_type]}</p>
                {!r.is_active && (
                  <Badge variant="outline" className="text-xs">
                    Paused
                  </Badge>
                )}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {formatSchedule(r)} · to {r.recipients.join(", ")}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Next: {formatDate(r.next_send_at)}
                {r.last_sent_at && ` · Last sent ${formatDate(r.last_sent_at)}`}
              </p>
            </div>

            {isAdmin && (
              <>
                <button
                  type="button"
                  onClick={() => toggleActive(r, !r.is_active)}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                    r.is_active ? "bg-primary" : "bg-muted"
                  }`}
                  aria-label="Toggle active"
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      r.is_active ? "translate-x-4" : "translate-x-1"
                    }`}
                  />
                </button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" className="h-8 w-8">
                      <MoreHorizontal size={14} />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onSelect={() => {
                        setEditing(r);
                        setDialogOpen(true);
                      }}
                    >
                      Edit
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => handleSendNow(r)}
                      disabled={sendingId === r.id}
                    >
                      {sendingId === r.id ? (
                        <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                      ) : (
                        <Send className="mr-2 h-3 w-3" />
                      )}
                      Send now
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => setDeleteTarget(r)}
                      className="text-destructive focus:text-destructive"
                    >
                      <Trash2 className="mr-2 h-3 w-3" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            )}
          </div>
        ))}
      </div>

      <ScheduledReportDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) setEditing(null);
        }}
        report={editing}
        defaultRecipient={user?.email}
      />

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete scheduled report?</AlertDialogTitle>
            <AlertDialogDescription>
              This stops the report from being sent. You can recreate it any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
