"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { BuilderCanvas, type SelectedKey } from "./builder-canvas";
import { Inspector } from "./inspector";
import { BuilderToolbar } from "./builder-toolbar";
import { TestRunner } from "./test-runner";
import { validateDraft } from "./validation";
import type { AutoflowDraft } from "@/lib/autoflow/types";

interface BuilderShellProps {
  initial: AutoflowDraft;
  mode: "create" | "edit";
  autoflowId?: string;
}

export function BuilderShell({ initial, mode, autoflowId }: BuilderShellProps) {
  const router = useRouter();
  const [draft, setDraft] = useState<AutoflowDraft>(initial);
  const [selected, setSelected] = useState<SelectedKey>({ kind: "trigger" });
  const [saving, setSaving] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const [savedAutoflowId, setSavedAutoflowId] = useState<string | undefined>(autoflowId);

  // Sync external initial state (e.g. when SWR resolves on /edit).
  useEffect(() => {
    setDraft(initial);
  }, [initial]);

  const validation = validateDraft(draft);

  const handleSave = useCallback(async () => {
    if (!validation.ok) {
      toast.error("Fix the issues before saving.");
      return;
    }
    setSaving(true);
    try {
      const body = serializeForApi(draft);
      const url =
        mode === "create"
          ? "/api/admin/autoflows"
          : `/api/admin/autoflows/${savedAutoflowId}`;
      const res = await fetch(url, {
        method: mode === "create" ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error((b as { detail?: string })?.detail || `Failed (${res.status})`);
      }
      const row = await res.json();
      toast.success(mode === "create" ? "Autoflow created" : "Autoflow saved");
      if (mode === "create" && row?.id) {
        setSavedAutoflowId(row.id);
        router.push(`/admin/autoflows/${row.id}`);
      } else if (mode === "edit" && row?.id) {
        router.refresh();
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }, [draft, mode, savedAutoflowId, router, validation.ok]);

  return (
    <div className="flex h-[100dvh] flex-col">
      <BuilderToolbar
        draft={draft}
        onUpdate={setDraft}
        validation={validation}
        saving={saving}
        onSave={handleSave}
        onTest={savedAutoflowId ? () => setTestOpen(true) : undefined}
        testDisabled={!savedAutoflowId}
        backHref="/admin/autoflows"
        saveLabel={mode === "create" ? "Create" : "Save"}
      />

      <div className="flex flex-1 overflow-hidden">
        <main className="flex-1 overflow-y-auto bg-muted/30 p-6 lg:p-10">
          <BuilderCanvas
            draft={draft}
            selectedKey={selected}
            onSelect={setSelected}
            onUpdate={setDraft}
          />
        </main>

        <Inspector
          draft={draft}
          selected={selected}
          onClose={() => setSelected(null)}
          onUpdate={setDraft}
        />
      </div>

      {savedAutoflowId && (
        <TestRunner
          open={testOpen}
          onOpenChange={setTestOpen}
          autoflowId={savedAutoflowId}
          draft={draft}
        />
      )}
    </div>
  );
}

export function serializeForApi(draft: AutoflowDraft) {
  return {
    name: draft.name.trim(),
    description: draft.description?.trim() || null,
    trigger_type: draft.trigger_type,
    trigger_config: {
      ...(draft.trigger_config.cron ? { cron: draft.trigger_config.cron } : {}),
      ...(draft.trigger_config.filters && Object.keys(draft.trigger_config.filters).length > 0
        ? { filters: draft.trigger_config.filters }
        : {}),
    },
    actions: draft.actions
      .slice()
      .sort((a, b) => a.order - b.order)
      .map((s, i) => ({
        type: s.type,
        config: s.config,
        order: i,
      })),
    confidence_threshold: draft.confidence_threshold,
    is_active: draft.is_active,
  };
}
