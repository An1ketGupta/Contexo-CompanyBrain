import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; stepKey: string }> },
) {
  const { id, stepKey } = await params;
  return proxyPostJson(
    request,
    `/onboarding/runs/${id}/steps/${encodeURIComponent(stepKey)}/edit-text`,
  );
}
