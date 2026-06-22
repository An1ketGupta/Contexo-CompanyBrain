import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(request: NextRequest) {
  return proxyJson(request, "/billing/portal-session", { method: "POST" });
}
