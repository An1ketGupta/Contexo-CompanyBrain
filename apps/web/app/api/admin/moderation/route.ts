import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const result = req.nextUrl.searchParams.get("result") ?? "all";
  const limit = req.nextUrl.searchParams.get("limit") ?? "50";
  return proxyJson(
    req,
    `/admin/moderation?result=${encodeURIComponent(result)}&limit=${encodeURIComponent(limit)}`,
  );
}
