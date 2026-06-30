import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ id: string }> }

export async function GET(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/recruiting/interview-kits/${id}`);
}

export async function PATCH(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/recruiting/interview-kits/${id}`, { method: "PATCH", body });
}

export async function DELETE(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/recruiting/interview-kits/${id}`, { method: "DELETE" });
}
