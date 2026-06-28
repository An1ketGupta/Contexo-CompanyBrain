/**
 * Naukri taxonomy proxy — passes through to FastAPI's
 * GET /integrations/ats/naukri/taxonomy/{kind} where kind ∈ {functional_areas,
 * role_categories, industries}. The backend validates the Literal so we
 * don't need to repeat the whitelist here.
 */
import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx {
  params: Promise<{ kind: string }>;
}

export async function GET(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { kind } = await params;
  return proxyJson(
    req,
    `/integrations/ats/naukri/taxonomy/${encodeURIComponent(kind)}`,
    { method: "GET" },
  );
}
