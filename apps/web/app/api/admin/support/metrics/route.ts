import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const windowDays = req.nextUrl.searchParams.get("window_days") ?? "30";
  return proxyJson(
    req,
    `/admin/support/metrics?window_days=${encodeURIComponent(windowDays)}`,
  );
}
