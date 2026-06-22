import { NextRequest } from "next/server";
import { proxyPublicJson } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  return proxyPublicJson(request, "/billing/plans");
}
