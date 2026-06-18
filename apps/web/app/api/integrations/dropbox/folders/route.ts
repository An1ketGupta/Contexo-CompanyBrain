import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const path = url.searchParams.get("path") || "";
  return proxyJson(
    req,
    `/integrations/dropbox/folders?path=${encodeURIComponent(path)}`,
  );
}
