"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck, Loader2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatRelativeShort, formatAbsolute } from "@/lib/date";
import { cn } from "@/lib/utils";
import { useNotifications, type Notification } from "@/hooks/use-notifications";

interface NotificationBellProps {
  className?: string;
}

/**
 * Header bell with unread count badge. Click opens a popover showing the
 * latest 20 notifications. Clicking a notification marks it read and
 * navigates to its link_url. "Mark all read" zeroes the badge.
 */
export function NotificationBell({ className }: NotificationBellProps) {
  const router = useRouter();
  const { notifications, unreadCount, isLoading, markRead, markAllRead } =
    useNotifications();

  const handleClick = async (n: Notification) => {
    if (!n.read_at) {
      // Fire-and-forget; the optimistic update handles UI feedback.
      void markRead(n.id);
    }
    if (n.link_url) {
      router.push(n.link_url);
    }
  };

  // Cap the badge at 9 — keeps the bell tidy and signals "lots of them" without
  // turning into a 3-digit eyesore.
  const badge = unreadCount > 9 ? "9+" : String(unreadCount);
  const hasUnread = unreadCount > 0;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={
            hasUnread
              ? `Notifications, ${unreadCount} unread`
              : "Notifications"
          }
          className={cn(
            "relative inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
        >
          <Bell className="h-4 w-4" />
          {hasUnread && (
            <span
              aria-hidden="true"
              className="absolute -top-0.5 -right-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[9px] font-semibold leading-none text-destructive-foreground"
            >
              {badge}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        sideOffset={6}
        className="w-80 p-0"
      >
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <p className="text-xs font-semibold text-foreground">Notifications</p>
          <button
            type="button"
            onClick={() => void markAllRead()}
            disabled={!hasUnread}
            className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline disabled:cursor-not-allowed disabled:opacity-40 disabled:no-underline"
          >
            <CheckCheck className="h-3 w-3" />
            Mark all read
          </button>
        </div>

        <div className="max-h-96 overflow-y-auto">
          {isLoading && notifications.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : notifications.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-muted-foreground">
              You&apos;re all caught up.
            </div>
          ) : (
            <ul className="divide-y divide-border/60">
              {notifications.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => void handleClick(n)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 px-3 py-2.5 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:bg-muted/60",
                      !n.read_at && "bg-primary/[0.04]",
                    )}
                  >
                    <div className="flex items-start gap-2">
                      {!n.read_at && (
                        <span
                          aria-hidden="true"
                          className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-primary"
                        />
                      )}
                      <p
                        className={cn(
                          "flex-1 text-xs leading-snug text-foreground",
                          !n.read_at && "font-medium",
                        )}
                      >
                        {n.title}
                      </p>
                    </div>
                    {n.body && (
                      <p className="line-clamp-2 pl-3.5 text-[11px] text-muted-foreground">
                        {n.body}
                      </p>
                    )}
                    <p
                      className="pl-3.5 text-[10px] text-muted-foreground/70"
                      title={formatAbsolute(n.created_at)}
                    >
                      {formatRelativeShort(n.created_at)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {notifications.length > 0 && (
          <div className="border-t border-border px-3 py-2 text-center">
            <Link
              href="/notifications"
              className="text-[11px] font-medium text-primary hover:underline"
            >
              View all notifications
            </Link>
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
