import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  return proxyJson(request, "/onboarding/docuseal-templates");
}
