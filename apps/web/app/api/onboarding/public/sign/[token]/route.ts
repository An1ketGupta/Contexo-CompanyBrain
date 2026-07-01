import { NextRequest } from "next/server";
import { ESIGN_API_URL, proxyPublicJson, proxyPublicPostJson } from "@/lib/api-proxy";

// Public signing form proxy — no auth (token lives in the URL). Points at
// apps/esign (a separate deploy from apps/api), not API_URL.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicJson(request, `/sign/${token}`, { baseUrl: ESIGN_API_URL });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicPostJson(request, `/sign/${token}`, { baseUrl: ESIGN_API_URL });
}
