import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const qs = url.searchParams.toString();
  return proxyJson(req, `/admin/announcements${qs ? `?${qs}` : ""}`);
}
