import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ id: string }> }

export async function PATCH(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/admin/knowledge-gap-clusters/${id}`, { method: "PATCH", body });
}
