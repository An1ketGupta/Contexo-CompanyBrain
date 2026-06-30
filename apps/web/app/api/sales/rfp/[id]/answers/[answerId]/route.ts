import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ id: string; answerId: string }> }

export async function PATCH(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id, answerId } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/sales/rfp/${id}/answers/${answerId}`, { method: "PATCH", body });
}
