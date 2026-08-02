import { NextRequest } from "next/server";
import { proxyPublicJson } from "@/lib/api-proxy";

// Public candidate document checklist — no auth (token lives in the URL path).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicJson(request, `/onboarding/public/documents/${token}`);
}
