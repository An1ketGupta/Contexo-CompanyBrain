/**
 * Catch-all proxy for ATS + Asana + Linear connect.
 * POST /integrations/{provider}/connect   (ATS only — body { api_key, ... })
 * GET  /integrations/{provider}/connect   (Asana, Linear — returns auth URL)
 *
 * Existing per-provider routes (gmail, drive, notion, slack, etc.)
 * sit higher in the routing tree and take precedence for those providers.
 */
import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ provider: string }> }

const KNOWN_PROVIDERS = new Set(["greenhouse", "lever", "ashby", "asana", "linear", "jira"]);

export async function POST(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { provider } = await params;
  if (!KNOWN_PROVIDERS.has(provider)) {
    return new Response(JSON.stringify({ code: "not_found" }), { status: 404 });
  }
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/integrations/${provider}/connect`, { method: "POST", body });
}

export async function GET(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { provider } = await params;
  if (!KNOWN_PROVIDERS.has(provider)) {
    return new Response(JSON.stringify({ code: "not_found" }), { status: 404 });
  }
  return proxyJson(req, `/integrations/${provider}/connect`);
}
