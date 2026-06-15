import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    // Empty body is fine — the backend defaults to using the stored draft.
    body = {};
  }
  return proxyJson(
    req,
    `/admin/document-drafts/${encodeURIComponent(id)}/approve`,
    { method: "POST", body },
  );
}
