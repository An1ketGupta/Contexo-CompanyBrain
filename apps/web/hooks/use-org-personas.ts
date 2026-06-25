"use client";

import useSWR from "swr";

export interface OrgPersona {
  id: string;
  name: string;
  description: string | null;
  instructions: string;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

interface ListResponse {
  personas: OrgPersona[];
}

const fetcher = async (url: string): Promise<ListResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load personas (${res.status})`);
  return res.json();
};

export function useOrgPersonas(options?: { includeArchived?: boolean }) {
  const url = options?.includeArchived
    ? "/api/org-personas?include_archived=true"
    : "/api/org-personas";
  const { data, error, isLoading, mutate } = useSWR<ListResponse>(url, fetcher, {
    revalidateOnFocus: false,
  });
  return {
    personas: data?.personas ?? [],
    loading: isLoading,
    error,
    refresh: mutate,
  };
}
