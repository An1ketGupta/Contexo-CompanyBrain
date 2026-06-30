import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ id: string; reqId: string }> }

export async function PATCH(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id, reqId } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/sales/rfp/${id}/requirements/${reqId}`, { method: "PATCH", body });
}

export async function DELETE(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id, reqId } = await params;
  return proxyJson(req, `/sales/rfp/${id}/requirements/${reqId}`, { method: "DELETE" });
}
