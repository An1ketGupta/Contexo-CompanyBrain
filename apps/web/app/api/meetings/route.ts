import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const sp = req.nextUrl.searchParams;
  const params = new URLSearchParams();
  params.set("limit", sp.get("limit") ?? "30");
  params.set("offset", sp.get("offset") ?? "0");
  return proxyJson(req, `/meetings?${params.toString()}`);
}
