import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, "/organizations/me/rfp-collection", { method: "PATCH", body });
}
