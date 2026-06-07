import { NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

async function getAccessToken(): Promise<string | null> {
  const cookieStore = await cookies();
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => cookieStore.getAll(),
        setAll: () => {},
      },
    },
  );
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function GET(): Promise<NextResponse> {
  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const upstream = await fetch(`${API_URL}/documents`, {
    headers: { Authorization: `Bearer ${token}` },
    next: { revalidate: 0 }, // always fetch fresh
  });

  const data = await upstream.json().catch(() => ({ documents: [] }));
  return NextResponse.json(data, { status: upstream.status });
}
