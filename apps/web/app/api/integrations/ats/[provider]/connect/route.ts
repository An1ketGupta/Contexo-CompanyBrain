import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx {
  params: Promise<{ provider: string }>;
}

export async function POST(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { provider } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/integrations/ats/${encodeURIComponent(provider)}/connect`, {
    method: "POST",
    body,
  });
}
