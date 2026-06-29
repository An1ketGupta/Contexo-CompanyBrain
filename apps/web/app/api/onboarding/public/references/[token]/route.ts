import { NextRequest } from "next/server";
import { proxyPublicJson, proxyPublicPostJson } from "@/lib/api-proxy";

// Public candidate-references form proxy — no auth (token lives in the URL).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicJson(request, `/onboarding/public/references/${token}`);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicPostJson(
    request,
    `/onboarding/public/references/${token}`,
  );
}
