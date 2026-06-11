import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  // Pass refresh=true through verbatim so the "Recompute" button on the admin
  // page can force a fresh score without us caching anything at the proxy layer.
  const refresh = req.nextUrl.searchParams.get("refresh") === "true";
  const path = refresh ? "/admin/coverage-score?refresh=true" : "/admin/coverage-score";
  return proxyJson(req, path);
}
