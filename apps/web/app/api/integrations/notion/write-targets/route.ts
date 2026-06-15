import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const q = url.searchParams.get("q");
  const path = q
    ? `/integrations/notion/write-targets?q=${encodeURIComponent(q)}`
    : "/integrations/notion/write-targets";
  return proxyJson(req, path);
}
