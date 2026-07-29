import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  const keys = request.nextUrl.searchParams.get("keys");
  const query = keys ? `?keys=${encodeURIComponent(keys)}` : "";
  return proxyJson(request, `/document-templates/readiness${query}`);
}
