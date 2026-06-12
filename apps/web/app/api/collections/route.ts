import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/collections", { method: "GET" });
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => null);
  return proxyJson(req, "/collections", { method: "POST", body: body ?? {} });
}
