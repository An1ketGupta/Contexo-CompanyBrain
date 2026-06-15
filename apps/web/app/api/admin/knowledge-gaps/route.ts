import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const days = req.nextUrl.searchParams.get("days") ?? "30";
  return proxyJson(req, `/admin/knowledge-gaps?days=${encodeURIComponent(days)}`);
}
