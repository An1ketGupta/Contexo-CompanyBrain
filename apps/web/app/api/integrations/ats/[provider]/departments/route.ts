import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx {
  params: Promise<{ provider: string }>;
}

export async function GET(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { provider } = await params;
  return proxyJson(req, `/integrations/ats/${encodeURIComponent(provider)}/departments`, {
    method: "GET",
  });
}
