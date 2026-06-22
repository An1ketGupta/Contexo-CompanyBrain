import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const params = req.nextUrl.searchParams;
  const qs = new URLSearchParams();
  const days = params.get("days");
  const reason = params.get("reason");
  const limit = params.get("limit");
  if (days) qs.set("days", days);
  if (reason) qs.set("reason", reason);
  if (limit) qs.set("limit", limit);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return proxyJson(req, `/admin/feedback-flagged${suffix}`);
}
