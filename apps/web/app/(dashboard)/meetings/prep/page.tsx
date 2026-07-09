import { Sparkles } from "lucide-react";

import { MeetingPrepPanel } from "@/components/meetings/prep-panel";

export default function MeetingPrepPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6 md:p-8">
      <header className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-tint text-brand">
          <Sparkles className="h-4 w-4" />
        </span>
        <h1 className="text-2xl font-extrabold tracking-tight">Meeting preparation</h1>
      </header>

      <MeetingPrepPanel />
    </div>
  );
}
