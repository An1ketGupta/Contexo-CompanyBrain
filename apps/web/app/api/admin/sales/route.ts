import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const { searchParams } = new URL(req.url);
  const qs = searchParams.toString();
  return proxyJson(req, `/admin/sales${qs ? `?${qs}` : ""}`);
}
