"use client";

import { useCallback, useMemo } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import type { PromptTemplate, TemplateCategory } from "@/lib/types";

interface TemplatesResponse {
  templates: PromptTemplate[];
}

export interface CreateTemplatePayload {
  title: string;
  description?: string | null;
  template_text: string;
  category: TemplateCategory;
  is_shared: boolean;
}

export interface UpdateTemplatePayload {
  title?: string;
  description?: string | null;
  template_text?: string;
  category?: TemplateCategory;
  is_shared?: boolean;
}

function buildKey(category?: TemplateCategory | "All", search?: string): string {
  const params = new URLSearchParams();
  if (category && category !== "All") params.set("category", category);
  if (search) params.set("search", search);
  const qs = params.toString();
  return qs ? `/api/templates?${qs}` : "/api/templates";
}

const fetcher = async (url: string): Promise<TemplatesResponse> => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return { templates: [] };
  return res.json();
};

/**
 * List + mutate templates. The list endpoint already fuses builtins / org-
 * shared / user-private via RLS, so the caller doesn't have to think about
 * visibility tiers — the templates returned are exactly what the user can use.
 *
 * After every mutation we revalidate every template-list cache key (with /
 * without filters) so an edit on the filtered "Email" view also flushes the
 * "All" tab. SWR keys are unique per filter combination, so a targeted mutate
 * here would miss the rest.
 */
export function useTemplates(
  category: TemplateCategory | "All" = "All",
  search: string = "",
) {
  const key = useMemo(() => buildKey(category, search), [category, search]);

  const { data, error, isLoading, mutate } = useSWR<TemplatesResponse>(
    key,
    fetcher,
    {
      revalidateOnFocus: true,
      keepPreviousData: true,
    },
  );

  // Invalidate every template list so a create / edit / delete reflects in
  // the popover regardless of the category tab the user has open.
  const invalidateAll = useCallback(() => {
    return globalMutate(
      (k: unknown) => typeof k === "string" && k.startsWith("/api/templates"),
      undefined,
      { revalidate: true },
    );
  }, []);

  const createTemplate = useCallback(
    async (payload: CreateTemplatePayload): Promise<PromptTemplate> => {
      const res = await fetch("/api/templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? "Failed to create template");
      }
      const json = (await res.json()) as { template: PromptTemplate };
      await invalidateAll();
      return json.template;
    },
    [invalidateAll],
  );

  const updateTemplate = useCallback(
    async (id: string, payload: UpdateTemplatePayload): Promise<PromptTemplate> => {
      const res = await fetch(`/api/templates/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? "Failed to update template");
      }
      const json = (await res.json()) as { template: PromptTemplate };
      await invalidateAll();
      return json.template;
    },
    [invalidateAll],
  );

  const deleteTemplate = useCallback(
    async (id: string): Promise<void> => {
      const res = await fetch(`/api/templates/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? body.message ?? "Failed to delete template");
      }
      await invalidateAll();
    },
    [invalidateAll],
  );

  const recordUse = useCallback(async (id: string): Promise<void> => {
    // Fire-and-forget — the popover already closed and selected; we don't
    // want the user to wait on a use_count++ before the textarea fills.
    fetch(`/api/templates/${id}/use`, { method: "POST" }).catch(() => {
      /* swallow — popularity sorting is best-effort */
    });
  }, []);

  return {
    templates: data?.templates ?? [],
    loading: isLoading,
    error: error ? "failed to load templates" : null,
    refresh: mutate,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    recordUse,
  };
}
