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
 *   generate — renders a template (LOI, appointment letter, NDA, induction)
 *   collect  — asks the candidate to upload documents; org-defined
 *   system   — background verification and policy acknowledgement
 *
 * `locked` steps can be neither disabled nor removed: the letter of intent
 * runs first and everything after it reads state it writes.
 */
export interface CatalogStep {
  step_key: string;
  kind: "generate" | "collect" | "system";
  label: string;
  description: string | null;
  document_type_key: string | null;
  bundle_key: string | null;
  bundle_label: string | null;
  position: number;
  enabled: boolean;
  signer_roles: string[];
  locked: boolean;
  items: CollectItem[];
}

export interface OnboardingCatalog {
  steps: CatalogStep[];
  /** False until the org saves a choice — gates the first-run setup screen. */
  configured: boolean;
}

/** What the editor sends when adding a document-collection step. */
export interface NewCollectStep {
  label: string;
  /** The step this one goes after. Null puts it first, behind the LOI. */
  after_step_key: string | null;
  items: Array<Omit<CollectItem, "item_key"> & { item_key?: string }>;
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

  return {
    catalog: data,
    steps: data?.steps ?? [],
    bundles: groupIntoBundles(data?.steps ?? []),
    isConfigured: data?.configured ?? false,
    isLoading,
    error,
    mutate,

    setStepEnabled: (stepKey: string, enabled: boolean) =>
      send(`${KEY}/steps/${encodeURIComponent(stepKey)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),

    renameStep: (stepKey: string, label: string) =>
      send(`${KEY}/steps/${encodeURIComponent(stepKey)}`, {
        method: "PATCH",
        body: JSON.stringify({ label }),
      }),

    addCollectStep: (step: NewCollectStep) =>
      send(KEY, { method: "POST", body: JSON.stringify(step) }),

    replaceItems: (stepKey: string, items: NewCollectStep["items"]) =>
      send(`${KEY}/steps/${encodeURIComponent(stepKey)}/items`, {
        method: "PUT",
        body: JSON.stringify({ items }),
      }),

    removeStep: (stepKey: string) =>
      send(`${KEY}/steps/${encodeURIComponent(stepKey)}`, { method: "DELETE" }),
  };
}
