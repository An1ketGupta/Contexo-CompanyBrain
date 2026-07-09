import { FileText } from "lucide-react";

import { PastMeetingsPanel } from "@/components/meetings/past-panel";

export default function MeetingTranscriptsPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6 md:p-8">
      <header className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-tint text-brand">
          <FileText className="h-4 w-4" />
        </span>
        <h1 className="text-2xl font-extrabold tracking-tight">Meeting transcripts</h1>
      </header>

      <PastMeetingsPanel />
    </div>
  );
}
