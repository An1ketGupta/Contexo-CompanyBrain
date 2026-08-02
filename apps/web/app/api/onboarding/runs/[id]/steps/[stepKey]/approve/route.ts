import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; stepKey: string }> },
): Promise<Response> {
  const { id, stepKey } = await params;
  return proxyPostJson(
    req,
    `/onboarding/runs/${id}/steps/${encodeURIComponent(stepKey)}/approve`,
  );
}
