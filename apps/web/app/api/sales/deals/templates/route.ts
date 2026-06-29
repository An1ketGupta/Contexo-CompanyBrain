import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(request: NextRequest) {
  return proxyPostJson(request, "/sales/deals/templates");
}
