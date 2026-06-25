"use client";

import { useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";

import { TriggerCard } from "./trigger-card";
import { ActionCard } from "./action-card";
import { StepConnector } from "./step-connector";
import { ActionPicker } from "./action-picker";
import { validateStep } from "./validation";
import type { ActionStep, ActionType, AutoflowDraft } from "@/lib/autoflow/types";
import { getAction } from "@/lib/autoflow/catalog";

interface BuilderCanvasProps {
  draft: AutoflowDraft;
  selectedKey: SelectedKey;
  onSelect: (key: SelectedKey) => void;
  onUpdate: (next: AutoflowDraft) => void;
}

export type SelectedKey =
  | { kind: "trigger" }
  | { kind: "action"; id: string }
  | null;

let _stepId = 0;
const newStepId = () => `step_${Date.now()}_${++_stepId}`;

export function BuilderCanvas({ draft, selectedKey, onSelect, onUpdate }: BuilderCanvasProps) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [insertIndex, setInsertIndex] = useState<number | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIdx = draft.actions.findIndex((s) => s.id === active.id);
    const newIdx = draft.actions.findIndex((s) => s.id === over.id);
    if (oldIdx === -1 || newIdx === -1) return;
    const reordered = arrayMove(draft.actions, oldIdx, newIdx).map((s, i) => ({
      ...s,
      order: i,
    }));
    onUpdate({ ...draft, actions: reordered });
  };

  const openPicker = (atIndex: number) => {
    setInsertIndex(atIndex);
    setPickerOpen(true);
  };

  const addAction = (type: ActionType) => {
    const step: ActionStep = {
      id: newStepId(),
      type,
      config: defaultConfig(type),
      order: 0,
    };
    const idx = insertIndex ?? draft.actions.length;
    const next = [...draft.actions];
    next.splice(idx, 0, step);
    const reindexed = next.map((s, i) => ({ ...s, order: i }));
    onUpdate({ ...draft, actions: reindexed });
    onSelect({ kind: "action", id: step.id });
  };

  const deleteAction = (id: string) => {
    const next = draft.actions
      .filter((s) => s.id !== id)
      .map((s, i) => ({ ...s, order: i }));
    onUpdate({ ...draft, actions: next });
    if (selectedKey?.kind === "action" && selectedKey.id === id) {
      onSelect(null);
    }
  };

  return (
    <div className="mx-auto w-full max-w-xl space-y-1">
      <TriggerCard
        triggerType={draft.trigger_type}
        triggerConfig={draft.trigger_config}
        selected={selectedKey?.kind === "trigger"}
        onClick={() => onSelect({ kind: "trigger" })}
      />

      <StepConnector onAdd={() => openPicker(0)} />

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={draft.actions.map((s) => s.id)} strategy={verticalListSortingStrategy}>
          {draft.actions.length === 0 ? (
            <EmptyState onAdd={() => openPicker(0)} />
          ) : (
            draft.actions.map((step, i) => {
              const invalid = !validateStep(step).ok;
              return (
                <div key={step.id}>
                  <ActionCard
                    step={step}
                    index={i}
                    selected={selectedKey?.kind === "action" && selectedKey.id === step.id}
                    invalid={invalid}
                    onClick={() => onSelect({ kind: "action", id: step.id })}
                    onDelete={() => deleteAction(step.id)}
                  />
                  <StepConnector onAdd={() => openPicker(i + 1)} />
                </div>
              );
            })
          )}
        </SortableContext>
      </DndContext>

      <ActionPicker open={pickerOpen} onOpenChange={setPickerOpen} onPick={addAction} />
    </div>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <button
      type="button"
      onClick={onAdd}
      className="flex w-full flex-col items-center gap-2 rounded-xl border border-dashed bg-card/50 p-8 text-center transition-colors hover:bg-card"
    >
      <span className="text-sm font-medium">Add your first action</span>
      <span className="text-xs text-muted-foreground">
        Pick what should happen when this trigger fires.
      </span>
    </button>
  );
}

function defaultConfig(type: ActionType): Record<string, unknown> {
  const entry = getAction(type);
  const cfg: Record<string, unknown> = {};
  for (const f of entry.fields) {
    if (f.type === "json") cfg[f.key] = {};
    else if (f.type === "tags") cfg[f.key] = [];
    else cfg[f.key] = "";
  }
  return cfg;
}
