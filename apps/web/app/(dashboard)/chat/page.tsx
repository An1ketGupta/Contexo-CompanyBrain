import { MessageSquare } from "lucide-react";

export default function ChatPage() {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
          <MessageSquare className="h-5 w-5" />
        </div>
        <h2 className="text-base font-semibold text-foreground">
          Chat is coming on Day 19
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The streaming task-execution UI will live here. Upload some documents
          on the Documents page so the AI has context to work with.
        </p>
      </div>
    </div>
  );
}
