import { NextRequest, NextResponse } from "next/server";
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

// Receives { filename, content_type, file_size } — no file body.
// Returns { doc_id, upload_url } — browser uploads directly to Supabase Storage.
export async function POST(request: NextRequest): Promise<NextResponse> {
  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => null);
  if (!body?.filename || !body?.content_type || body?.file_size == null) {
    return NextResponse.json({ error: "Missing filename, content_type, or file_size" }, { status: 400 });
  }

  const upstream = await fetch(`${API_URL}/documents/upload/init`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, { status: upstream.status });
}
