import { Settings as SettingsIcon } from "lucide-react";

export default function SettingsPage() {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-sm text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
          <SettingsIcon className="h-5 w-5" />
        </div>
        <h2 className="text-base font-semibold text-foreground">
          Settings is coming on Day 22
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Workspace and member management will live here.
        </p>
      </div>
    </div>
  );
}
