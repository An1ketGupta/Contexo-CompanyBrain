"use client";

import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TriggerForm } from "./forms/trigger-form";
import { ActionForm } from "./forms/action-form";
import type { SelectedKey } from "./builder-canvas";
import type { ActionStep, AutoflowDraft } from "@/lib/autoflow/types";

interface InspectorProps {
  draft: AutoflowDraft;
  selected: SelectedKey;
  onClose: () => void;
  onUpdate: (next: AutoflowDraft) => void;
}

export function Inspector({ draft, selected, onClose, onUpdate }: InspectorProps) {
  if (!selected) {
    return (
      <aside className="hidden h-full w-[400px] shrink-0 border-l bg-card/50 lg:flex lg:flex-col">
        <div className="flex items-center justify-center p-10 text-center">
          <div className="space-y-2 text-muted-foreground">
            <p className="text-sm font-medium">Nothing selected</p>
            <p className="text-xs">
              Click the trigger or any step to edit it here.
            </p>
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside className="fixed inset-y-0 right-0 z-30 w-full max-w-[440px] border-l bg-background shadow-xl lg:static lg:z-auto lg:max-w-none lg:w-[440px] lg:shrink-0 lg:shadow-none">
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">
            {selected.kind === "trigger" ? "Trigger settings" : "Step settings"}
          </h2>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close inspector">
            <X className="size-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {selected.kind === "trigger" ? (
            <TriggerForm draft={draft} onUpdate={onUpdate} />
          ) : (
            <ActionPanel id={selected.id} draft={draft} onUpdate={onUpdate} />
          )}
        </div>
      </div>
    </aside>
  );
}

function ActionPanel({
  id,
  draft,
  onUpdate,
}: {
  id: string;
  draft: AutoflowDraft;
  onUpdate: (next: AutoflowDraft) => void;
}) {
  const step = draft.actions.find((s) => s.id === id);
  if (!step) {
    return <p className="text-xs text-muted-foreground">Step not found.</p>;
  }
  const setStep = (next: ActionStep) => {
    onUpdate({
      ...draft,
      actions: draft.actions.map((s) => (s.id === id ? next : s)),
    });
  };
  return <ActionForm step={step} draft={draft} onUpdate={setStep} />;
}
