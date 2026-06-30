import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface RouteCtx { params: Promise<{ id: string }> }

export async function POST(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/recruiting/interview-kits/${id}/publish`, { method: "POST" });
}
