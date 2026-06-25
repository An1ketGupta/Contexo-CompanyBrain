import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ provider: string }> }

const KNOWN = new Set(["greenhouse", "lever", "ashby", "asana", "linear", "jira"]);

export async function DELETE(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { provider } = await params;
  if (!KNOWN.has(provider)) {
    return new Response(JSON.stringify({ code: "not_found" }), { status: 404 });
  }
  return proxyJson(req, `/integrations/${provider}`, { method: "DELETE" });
}
