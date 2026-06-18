import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ siteId: string }> },
): Promise<Response> {
  const { siteId } = await params;
  return proxyJson(
    req,
    `/integrations/onedrive/sites/${encodeURIComponent(siteId)}/drives`,
  );
}
