"use client";

import useSWR from "swr";

export type BuiltInPersona =
  | "hr"
  | "sales"
  | "engineering"
  | "finance"
  | "operations"
  | "executive";

// Feature 1.11 — persona is now an open string. The literal union exists for
// IDE help on the built-ins; "custom" and `org:<uuid>` are also valid.
export type UserPersona = BuiltInPersona | "custom" | string;

export interface CurrentUser {
  id: string;
  email: string | null;
  display_name: string | null;
  role: "admin" | "member";
  org_id: string | null;
  activity_private?: boolean;
  persona?: UserPersona | null;
  custom_persona_name?: string | null;
  custom_persona_instructions?: string | null;
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
  if (res.status === 401) {
    if (typeof window !== "undefined") {
      const next = encodeURIComponent(
        window.location.pathname + window.location.search,
      );
      window.location.href = `/login?redirectedFrom=${next}`;
    }
    throw new Error("Unauthorized");
  }
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
