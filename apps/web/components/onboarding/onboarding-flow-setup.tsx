"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { StepCatalogEditor } from "@/components/onboarding/step-catalog-editor";

/**
 * First-run screen: the org reviews its onboarding flow before it can start a
 * run. Shown in place of the runs list until a choice is saved — an org that
 * has never decided and an org that deliberately runs everything look
 * identical in the catalog, so the pipeline shouldn't guess which one it is.
 *
 * Everything starts on and every step is editable here, so accepting the
 * defaults is one click.
 */
export function OnboardingFlowSetup({
  isAdmin,
  onSaved,
}: {
  isAdmin: boolean;
  onSaved: () => void;
}) {
  const [saving, setSaving] = useState(false);

  async function confirm() {
    setSaving(true);
    try {
      const res = await fetch("/api/onboarding/catalog/confirm", {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || body?.message || "Couldn't save your flow.");
      }
      toast.success("Onboarding flow created.");
      onSaved();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Couldn't save your flow.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <div className="rounded-2xl border border-border bg-card">
        <header className="border-b border-border px-6 py-5">
          <h2 className="text-lg font-bold tracking-tight text-foreground">
            Set up your onboarding flow
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
            Every company onboards differently. Turn off the steps yours
            doesn&apos;t run, and add any documents you collect from
            candidates. You can change all of this later in Settings.
          </p>
        </header>

        <div className="px-6 py-5">
          <StepCatalogEditor canEdit={isAdmin} />
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-6 py-4">
          <p className="text-xs text-muted-foreground">
            {isAdmin
              ? "You can change any of this later."
              : "Only workspace admins can set this up."}
          </p>
          <Button onClick={confirm} disabled={!isAdmin || saving}>
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Creating
              </>
            ) : (
              "Create onboarding flow"
            )}
          </Button>
        </footer>
      </div>
    </div>
  );
}
