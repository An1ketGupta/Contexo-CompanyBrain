import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

// Light-weight document listing for the onboarding templates picker. Avoids
// taking a hard dependency on the (heavier) documents page hooks.
export async function GET(request: NextRequest) {
  return proxyJson(request, "/documents");
}
