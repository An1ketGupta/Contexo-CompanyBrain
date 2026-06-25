import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/admin/knowledge-health/settings");
}

export async function PATCH(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, "/admin/knowledge-health/settings", {
    method: "PATCH",
    body,
  });
}
