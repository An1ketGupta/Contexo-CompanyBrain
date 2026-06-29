import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  const search = request.nextUrl.search;
  return proxyJson(request, `/sales/deals/runs${search}`);
}

export async function POST(request: NextRequest) {
  return proxyPostJson(request, "/sales/deals/runs");
}
