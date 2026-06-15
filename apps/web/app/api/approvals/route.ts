import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const sp = req.nextUrl.searchParams;
  const tab = sp.get("tab") ?? "inbox";
  const status = sp.get("status") ?? "pending";
  const limit = sp.get("limit") ?? "50";
  const qs = new URLSearchParams({ tab, status, limit }).toString();
  return proxyJson(req, `/approvals?${qs}`);
}

export async function POST(req: NextRequest): Promise<Response> {
  return proxyPostJson(req, "/approvals");
}
