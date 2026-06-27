import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, "/integrations/ats/preview-mapping", { method: "POST", body });
}
