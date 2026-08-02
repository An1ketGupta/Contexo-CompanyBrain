"use client";

import useSWR from "swr";

/** One document on a collection step's checklist. */
export interface CollectItem {
  item_key: string;
  label: string;
  help_text: string | null;
  required: boolean;
  accepted_formats: string[];
}

/**
 * A step in the org's onboarding pipeline.
 *
 * `kind` decides what the step does and what can be edited about it:
 *   generate — sends official documents, rendered from templates
 *   collect  — asks the candidate to upload documents
 *   system   — background verification or policy acknowledgement, per
 *              `system_action`
 *
 * Nothing is locked as of migration 108 — `locked` remains the mechanism if a
 * step ever again has to lead, and the editor honours it.
 */
export interface CatalogStep {
  step_key: string;
  kind: StepKind;
  label: string;
  description: string | null;
  document_type_key: string | null;
  bundle_key: string | null;
  bundle_label: string | null;
  position: number;
  enabled: boolean;
  /** Routing order — whoever is first signs first. Empty means unsigned. */
  signer_roles: string[];
  system_action: SystemAction | null;
  locked: boolean;
  items: CollectItem[];
}

export type StepKind = "generate" | "collect" | "system";
export type SystemAction = "bgv" | "policies";
export type SignerRole = "hr" | "candidate";

/** A template the org can put in a "send official documents" step. */
export interface DocumentType {
  key: string;
  label: string;
  description: string | null;
  /** False when no default template is uploaded yet — offered, but flagged. */
  has_template: boolean;
}

export interface OnboardingCatalog {
  steps: CatalogStep[];
  /** False until the org saves a choice — gates the first-run setup screen. */
  configured: boolean;
  document_types: DocumentType[];
}

export type DraftItem = Omit<CollectItem, "item_key"> & { item_key?: string };

/** What the builder sends when adding a step of any kind. */
export interface NewStep {
  kind: StepKind;
  label: string;
  /** The step this one goes after. Null puts it first in the pipeline. */
  after_step_key: string | null;
  items?: DraftItem[];
  /** More than one makes a bundle: generated, reviewed and signed together. */
  document_type_keys?: string[];
  signer_roles?: SignerRole[];
  system_action?: SystemAction;
}

const KEY = "/api/onboarding/catalog";

const fetcher = async (url: string): Promise<OnboardingCatalog> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load (${res.status})`);
  return res.json();
};

async function readError(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  return body?.detail || body?.message || `Request failed (${res.status})`;
}

/**
 * Bundle members are asked, reviewed and sent as one unit, so the editor shows
 * one row for the bundle rather than one per document. Unbundled steps come
 * back as a group of one, so callers never branch on null.
 */
export function groupIntoBundles(steps: CatalogStep[]): CatalogStep[][] {
  const groups: CatalogStep[][] = [];
  const seen = new Set<string>();
  for (const step of [...steps].sort((a, b) => a.position - b.position)) {
    if (!step.bundle_key) {
      groups.push([step]);
      continue;
    }
    if (seen.has(step.bundle_key)) continue;
    seen.add(step.bundle_key);
    groups.push(steps.filter((s) => s.bundle_key === step.bundle_key));
  }
  return groups;
}

export function useOnboardingCatalog() {
  const { data, error, isLoading, mutate } = useSWR<OnboardingCatalog>(
    KEY,
    fetcher,
    { revalidateOnFocus: false },
  );

  /** Every mutation returns the whole catalog, so they all settle the same way. */
  async function send(
    url: string,
    init: RequestInit,
  ): Promise<OnboardingCatalog> {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
    if (!res.ok) throw new Error(await readError(res));
    const next: OnboardingCatalog = await res.json();
    await mutate(next, { revalidate: false });
    return next;
  }

  const patch = (stepKey: string, body: Record<string, unknown>) =>
    send(`${KEY}/steps/${encodeURIComponent(stepKey)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });

  return {
    catalog: data,
    steps: data?.steps ?? [],
    bundles: groupIntoBundles(data?.steps ?? []),
    documentTypes: data?.document_types ?? [],
    isConfigured: data?.configured ?? false,
    isLoading,
    error,
    mutate,

    setStepEnabled: (stepKey: string, enabled: boolean) =>
      patch(stepKey, { enabled }),

    renameStep: (stepKey: string, label: string) => patch(stepKey, { label }),

    setSigners: (stepKey: string, signer_roles: SignerRole[]) =>
      patch(stepKey, { signer_roles }),

    moveStep: (stepKey: string, move: "up" | "down") =>
      patch(stepKey, { move }),

    addStep: (step: NewStep) =>
      send(KEY, { method: "POST", body: JSON.stringify(step) }),

    replaceItems: (stepKey: string, items: DraftItem[]) =>
      send(`${KEY}/steps/${encodeURIComponent(stepKey)}/items`, {
        method: "PUT",
        body: JSON.stringify({ items }),
      }),

    removeStep: (stepKey: string) =>
      send(`${KEY}/steps/${encodeURIComponent(stepKey)}`, { method: "DELETE" }),
  };
}
