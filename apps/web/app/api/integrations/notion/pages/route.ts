import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const qs = req.nextUrl.search;
  return proxyJson(req, `/integrations/notion/pages${qs}`);
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json();
  return proxyJson(req, "/integrations/notion/pages", { method: "POST", body });
}
