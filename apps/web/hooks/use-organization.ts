"use client";

import useSWR from "swr";
import { networkError, parseApiError, type ApiError } from "@/lib/errors";

export interface OrganizationMeta {
  id: string;
  name: string;
  slug: string;
  plan: string;
  created_at: string;
  industry: string | null;
  company_size: string | null;
  primary_use_case: string | null;
  onboarding_completed_at: string | null;
}

const fetcher = async (url: string): Promise<OrganizationMeta> => {
  let res: Response;
  try {
    res = await fetch(url);
  } catch (err) {
    throw networkError(err);
  }
  if (!res.ok) throw await parseApiError(res);
  return res.json();
};

/** Read-side hook for the V5 enrichment metadata. Used by the EnrichmentModal
 *  trigger so it knows whether to show, and could be reused by future
 *  personalization features. */
export function useOrganization() {
  const { data, error, isLoading, mutate } = useSWR<OrganizationMeta, ApiError>(
    "/api/organizations/me",
    fetcher,
    { revalidateOnFocus: false },
  );
  return {
    organization: data ?? null,
    loading: isLoading,
    error: error ? error.message : null,
    refresh: mutate,
  };
}
