"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CalendarDays } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UpcomingMeetingsPanel } from "@/components/meetings/upcoming-panel";
import { PastMeetingsPanel } from "@/components/meetings/past-panel";
import { MeetingPrepPanel } from "@/components/meetings/prep-panel";

const TABS = ["upcoming", "past", "prep"] as const;
type Tab = (typeof TABS)[number];

function MeetingsTabs() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const param = searchParams.get("tab");
  const active: Tab = TABS.includes(param as Tab) ? (param as Tab) : "upcoming";

  const setTab = (value: string) => {
    const next = new URLSearchParams(searchParams.toString());
    next.set("tab", value);
    router.replace(`/meetings?${next.toString()}`, { scroll: false });
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-tint text-brand">
          <CalendarDays className="h-4 w-4" />
        </span>
        <h1 className="text-2xl font-extrabold tracking-tight">Meetings</h1>
      </header>

      <Tabs value={active} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="upcoming">Upcoming</TabsTrigger>
          <TabsTrigger value="past">Past meetings</TabsTrigger>
          <TabsTrigger value="prep">Prep</TabsTrigger>
        </TabsList>

        <TabsContent value="upcoming" className="mt-6">
          <UpcomingMeetingsPanel />
        </TabsContent>
        <TabsContent value="past" className="mt-6">
          <PastMeetingsPanel />
        </TabsContent>
        <TabsContent value="prep" className="mt-6">
          <MeetingPrepPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function MeetingsPage() {
  return (
    <Suspense fallback={null}>
      <MeetingsTabs />
    </Suspense>
  );
}
