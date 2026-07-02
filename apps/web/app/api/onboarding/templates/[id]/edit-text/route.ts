import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxyPostJson(request, `/onboarding/templates/${id}/edit-text`);
}
