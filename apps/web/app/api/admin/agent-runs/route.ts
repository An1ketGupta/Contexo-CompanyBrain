import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const sp = req.nextUrl.searchParams;
  const params = new URLSearchParams();
  const agent_type = sp.get("agent_type");
  const status = sp.get("status");
  const limit = sp.get("limit") ?? "50";
  const offset = sp.get("offset") ?? "0";
  if (agent_type) params.set("agent_type", agent_type);
  if (status) params.set("status", status);
  params.set("limit", limit);
  params.set("offset", offset);
  return proxyJson(req, `/admin/agent-runs?${params.toString()}`);
}
