"use client";

import Link from "next/link";
import { Brain } from "lucide-react";
import { SidebarNav } from "./sidebar-nav";
import { UserMenu } from "./user-menu";
import { QuotaMeter } from "./quota-meter";
import { ActivityFeed } from "./activity-feed";
import { CommandPaletteTrigger } from "./command-palette-trigger";
import { OnboardingChecklist } from "@/components/onboarding/onboarding-checklist";
import { useCurrentUser } from "@/hooks/use-user";

export function Sidebar() {
  const { user, organization } = useCurrentUser();

  return (
    <aside className="hidden h-screen w-60 shrink-0 flex-col border-r border-border bg-background md:flex">
      <Link
        href="/chat"
        className="flex h-14 items-center gap-2 border-b border-border px-5"
      >
        <Brain className="h-5 w-5 text-primary" />
        <span className="text-sm font-semibold tracking-tight">
          Company Brain
        </span>
      </Link>

      <div className="pt-2">
        <CommandPaletteTrigger />
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        <SidebarNav />
      </div>

      {/* Onboarding sits between the nav and the quota meter so it's visible
          on every dashboard page without scrolling. Renders nothing once
          dismissed or once all steps are derived as complete + confetti has
          fired. */}
      <OnboardingChecklist />

      <ActivityFeed />

      <QuotaMeter />

      <div className="border-t border-border p-2">
        <UserMenu user={user} organization={organization} />
      </div>
    </aside>
  );
}
