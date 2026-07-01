"use client";

import { useState } from "react";
import Link from "next/link";
import { Brain, Menu } from "lucide-react";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { SidebarNav } from "./sidebar-nav";
import { UserMenu } from "./user-menu";
import { NotificationBell } from "./notification-bell";
import { useCurrentUser } from "@/hooks/use-user";

export function MobileHeader() {
  const [open, setOpen] = useState(false);
  const { user, organization } = useCurrentUser();

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-background px-4 md:hidden">
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Open menu">
            <Menu />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="flex w-64 flex-col p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Link
            href="/chat"
            onClick={() => setOpen(false)}
            className="flex h-14 items-center gap-2 border-b border-border px-5"
          >
            <Brain className="h-5 w-5 text-primary" />
            <span className="text-sm font-semibold tracking-tight">
              Nirnaya IQ
            </span>
          </Link>
          <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto py-2">
            <SidebarNav onNavigate={() => setOpen(false)} />
          </div>
          {/* `pb-safe-2` keeps the user menu clear of the iOS home indicator
              while preserving the base 0.5rem padding on devices without an
              inset. */}
          <div className="border-t border-border p-2 pb-safe-2">
            <UserMenu user={user} organization={organization} />
          </div>
        </SheetContent>
      </Sheet>

      <Link href="/chat" className="flex items-center gap-2">
        <Brain className="h-5 w-5 text-primary" />
        <span className="text-sm font-semibold tracking-tight">
          Nirnaya IQ
        </span>
      </Link>

      <NotificationBell />
    </header>
  );
}
