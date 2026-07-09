import { CalendarDays } from "lucide-react";

import { UpcomingMeetingsPanel } from "@/components/meetings/upcoming-panel";

export default function UpcomingMeetingsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-tint text-brand">
          <CalendarDays className="h-4 w-4" />
        </span>
        <h1 className="text-2xl font-extrabold tracking-tight">Upcoming meetings</h1>
      </header>

      <UpcomingMeetingsPanel />
    </div>
  );
}
