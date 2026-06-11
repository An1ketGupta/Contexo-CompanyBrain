import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const limit = req.nextUrl.searchParams.get("limit") ?? "20";
  return proxyJson(req, `/team/activity?limit=${encodeURIComponent(limit)}`);
}
