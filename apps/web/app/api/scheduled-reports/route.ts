import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/scheduled-reports");
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, "/scheduled-reports", { method: "POST", body });
}
