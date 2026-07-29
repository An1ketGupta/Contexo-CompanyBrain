import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("onboarding_run_id");
  const query = runId
    ? `?onboarding_run_id=${encodeURIComponent(runId)}`
    : "";
  return proxyJson(request, `/generated-documents${query}`);
}

export async function POST(request: NextRequest) {
  return proxyPostJson(request, "/generated-documents");
}
