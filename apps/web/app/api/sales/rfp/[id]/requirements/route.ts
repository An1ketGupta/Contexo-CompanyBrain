import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ id: string }> }

export async function PATCH(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/sales/rfp/${id}/requirements`, { method: "PATCH", body });
}

export async function POST(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/sales/rfp/${id}/requirements`, { method: "POST", body });
}
