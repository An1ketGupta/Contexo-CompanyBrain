import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const q = url.searchParams.get("q");
  const path = `/integrations/onedrive/sites${q ? `?q=${encodeURIComponent(q)}` : ""}`;
  return proxyJson(req, path);
}
