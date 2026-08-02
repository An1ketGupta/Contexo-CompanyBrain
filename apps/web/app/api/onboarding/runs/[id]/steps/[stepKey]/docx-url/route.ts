import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; stepKey: string }> },
) {
  const { id, stepKey } = await params;
  return proxyJson(
    request,
    `/onboarding/runs/${id}/steps/${encodeURIComponent(stepKey)}/docx-url`,
    { method: "GET" },
  );
}
