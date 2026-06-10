import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/webhooks");
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json();
  return proxyJson(req, "/webhooks", { method: "POST", body });
}
