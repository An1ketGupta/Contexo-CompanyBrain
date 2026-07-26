import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxyJson(request, `/onboarding/templates/${id}/slots`);
}

// Adds a fill-point HR located by hand, for blanks detection couldn't see.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxyPostJson(request, `/onboarding/templates/${id}/slots`);
}
