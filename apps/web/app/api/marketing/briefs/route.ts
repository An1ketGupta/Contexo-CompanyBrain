import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const qs = url.searchParams.toString();
  return proxyJson(req, `/marketing/briefs${qs ? `?${qs}` : ""}`);
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, "/marketing/briefs/generate", { method: "POST", body });
}
