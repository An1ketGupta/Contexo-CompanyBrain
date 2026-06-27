"use client";

import Link from "next/link";
import { Brain } from "lucide-react";
import { SidebarNav } from "./sidebar-nav";
import { UserMenu } from "./user-menu";
import { QuotaMeter } from "./quota-meter";
import { CommandPaletteTrigger } from "./command-palette-trigger";
import { NotificationBell } from "./notification-bell";
import { useCurrentUser } from "@/hooks/use-user";

export function Sidebar() {
  const { user, organization } = useCurrentUser();

  return (
    <aside className="hidden h-dvh w-60 shrink-0 flex-col border-r border-border bg-background md:flex">
      <div className="flex h-14 items-center justify-between border-b border-border px-3">
        <Link href="/chat" className="flex items-center gap-2 px-2">
          <Brain className="h-5 w-5 text-primary" />
          <span className="text-sm font-semibold tracking-tight">
            Company Brain
          </span>
        </Link>
        <NotificationBell />
      </div>

      <div className="pt-2">
        <CommandPaletteTrigger />
      </div>

      <div
        className="scrollbar-thin min-h-0 flex-1 overflow-y-auto py-2"
        style={{ scrollbarGutter: "stable" }}
      >
        <SidebarNav />
      </div>

      <QuotaMeter />

      <div className="border-t border-border p-2">
        <UserMenu user={user} organization={organization} />
      </div>

      <div className="flex items-center justify-center gap-3 border-t border-border px-3 py-2 text-[10px] text-muted-foreground">
        <Link
          href="/privacy"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-foreground"
        >
          Privacy
        </Link>
        <span aria-hidden>·</span>
        <Link
          href="/terms"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-foreground"
        >
          Terms
        </Link>
      </div>
    </aside>
  );
}
