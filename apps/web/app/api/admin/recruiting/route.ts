import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const period = req.nextUrl.searchParams.get("period") ?? "30d";
  return proxyJson(req, `/admin/recruiting?period=${encodeURIComponent(period)}`);
}
