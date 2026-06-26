import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/channels");
}

export async function POST(req: NextRequest): Promise<Response> {
  return proxyPostJson(req, "/channels");
}
