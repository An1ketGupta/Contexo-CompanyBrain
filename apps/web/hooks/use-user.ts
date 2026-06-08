"use client";

import useSWR from "swr";

export interface CurrentUser {
  id: string;
  email: string | null;
  display_name: string | null;
  role: "admin" | "member";
  org_id: string | null;
}

export interface CurrentOrg {
  id: string;
  name: string;
  slug: string;
  plan: string;
}

interface MeResponse {
  user: CurrentUser;
  organization: CurrentOrg | null;
}

const fetcher = async (url: string): Promise<MeResponse> => {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load profile (${res.status})`);
  return res.json();
};

export function useCurrentUser() {
  const { data, error, isLoading, mutate } = useSWR<MeResponse>(
    "/api/me",
    fetcher,
    { revalidateOnFocus: false },
  );

  return {
    user: data?.user ?? null,
    organization: data?.organization ?? null,
    loading: isLoading,
    error,
    refresh: mutate,
  };
}
