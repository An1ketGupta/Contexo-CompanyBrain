import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const qs = url.search;
  return proxyJson(req, `/admin/quality-metrics/backfill${qs}`, { method: "POST", body: {} });
}
