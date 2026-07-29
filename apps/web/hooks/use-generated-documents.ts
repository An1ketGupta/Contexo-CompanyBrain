"use client";

import { useCallback } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import type { GeneratedDocument, GenerateResult } from "@/lib/types";

async function readError(res: Response, fallback: string): Promise<never> {
  const body = await res.json().catch(() => ({}));
  throw new Error(body.detail ?? body.message ?? fallback);
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) await readError(res, "Request failed");
  return res.json();
}

function invalidateAll() {
  return globalMutate(
    (key: unknown) =>
      typeof key === "string" && key.startsWith("/api/generated-documents"),
    undefined,
    { revalidate: true },
  );
}

export function useGeneratedDocuments(onboardingRunId?: string | null) {
  const key = onboardingRunId
    ? `/api/generated-documents?onboarding_run_id=${onboardingRunId}`
    : "/api/generated-documents";

  const { data, error, isLoading, mutate } = useSWR<GeneratedDocument[]>(
    key,
    getJson,
  );

  /**
   * Generate a document.
   *
   * Never throws for an expected failure — a missing template, a field HR
   * hasn't filled in, a template edited since its fields were confirmed all
   * come back as an `outcome` for the caller to render. Only a transport or
   * auth failure raises.
   */
  const generate = useCallback(
    async (input: {
      template_id?: string;
      type_key?: string;
      onboarding_run_id?: string;
      candidate_id?: string;
      requisition_id?: string;
      overrides?: Record<string, unknown>;
    }): Promise<GenerateResult> => {
      const res = await fetch("/api/generated-documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!res.ok) await readError(res, "Couldn't generate the document");
      const result = (await res.json()) as GenerateResult;
      await invalidateAll();
      return result;
    },
    [],
  );

  return { documents: data ?? [], error, isLoading, mutate, generate };
}

export function useGeneratedDocument(documentId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<GeneratedDocument>(
    documentId ? `/api/generated-documents/${documentId}` : null,
    getJson,
  );

  const approve = useCallback(async () => {
    if (!documentId) return;
    const res = await fetch(`/api/generated-documents/${documentId}/approve`, {
      method: "POST",
    });
    if (!res.ok) await readError(res, "Couldn't approve the document");
    await mutate();
    await invalidateAll();
  }, [documentId, mutate]);

  const reject = useCallback(
    async (reason?: string) => {
      if (!documentId) return;
      const res = await fetch(`/api/generated-documents/${documentId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason ?? null }),
      });
      if (!res.ok) await readError(res, "Couldn't reject the document");
      await mutate();
      await invalidateAll();
    },
    [documentId, mutate],
  );

  return { document: data, error, isLoading, mutate, approve, reject };
}
