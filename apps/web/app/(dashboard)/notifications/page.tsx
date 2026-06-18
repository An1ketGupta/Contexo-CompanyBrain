"use client";

import { useRouter } from "next/navigation";
import { Bell, CheckCheck } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatAbsolute, formatRelativeShort } from "@/lib/date";
import { cn } from "@/lib/utils";
import { useNotifications, type Notification } from "@/hooks/use-notifications";

export default function NotificationsPage() {
  const router = useRouter();
  const {
    notifications,
    unreadCount,
    isLoading,
    markRead,
    markAllRead,
  } = useNotifications({ limit: 100 });

  const handleClick = async (n: Notification) => {
    if (!n.read_at) {
      void markRead(n.id);
    }
    if (n.link_url) {
      router.push(n.link_url);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <Bell className="h-5 w-5 text-primary" />
            Notifications
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {unreadCount > 0
              ? `${unreadCount} unread`
              : "All caught up."}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void markAllRead()}
          disabled={unreadCount === 0}
          className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-background"
        >
          <CheckCheck className="h-3.5 w-3.5" />
          Mark all read
        </button>
      </header>

      {isLoading && notifications.length === 0 ? (
        <NotificationsSkeleton />
      ) : notifications.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-background px-4 py-16 text-center">
          <Bell className="mx-auto h-8 w-8 text-muted-foreground/40" />
          <p className="mt-3 text-sm font-medium text-foreground">
            You&apos;re all caught up
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            New reminders and alerts will appear here.
          </p>
        </div>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border bg-background">
          {notifications.map((n) => (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => void handleClick(n)}
                className={cn(
                  "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none",
                  !n.read_at && "bg-primary/[0.04]",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                    n.read_at ? "bg-transparent" : "bg-primary",
                  )}
                />
                <div className="min-w-0 flex-1">
                  <p
                    className={cn(
                      "text-sm leading-snug text-foreground",
                      !n.read_at && "font-medium",
                    )}
                  >
                    {n.title}
                  </p>
                  {n.body && (
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {n.body}
                    </p>
                  )}
                  <p
                    className="mt-1 text-[11px] uppercase tracking-wider text-muted-foreground/70"
                    title={formatAbsolute(n.created_at)}
                  >
                    <span>{formatRelativeShort(n.created_at)}</span>
                    <span className="mx-1.5">·</span>
                    <span className="normal-case tracking-normal">
                      {humanizeType(n.type)}
                    </span>
                  </p>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function humanizeType(type: string): string {
  return type.replace(/_/g, " ");
}

function NotificationsSkeleton() {
  return (
    <ul className="divide-y divide-border rounded-lg border border-border bg-background">
      {Array.from({ length: 6 }).map((_, i) => (
        <li key={i} className="flex items-start gap-3 px-4 py-3">
          <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-muted" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className="h-4 w-3/5" />
            <Skeleton className="h-3 w-4/5" />
            <Skeleton className="h-3 w-24" />
          </div>
        </li>
      ))}
    </ul>
  );
}
