import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx {
  params: Promise<{ provider: string }>;
}

export async function POST(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { provider } = await params;
  return proxyJson(req, `/integrations/ats/${encodeURIComponent(provider)}/refresh-mapping`, {
    method: "POST",
  });
}
