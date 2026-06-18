import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const cloudId = url.searchParams.get("cloud_id") || "";
  return proxyJson(
    req,
    `/integrations/confluence/spaces?cloud_id=${encodeURIComponent(cloudId)}`,
  );
}
