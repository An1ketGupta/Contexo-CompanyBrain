import { NextRequest, NextResponse } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  // Preserve the query string so category / search filters land on the upstream.
  const search = new URL(req.url).search;
  return proxyJson(req, `/templates${search}`);
}

export async function POST(req: NextRequest): Promise<Response> {
  return proxyPostJson(req, "/templates");
}
