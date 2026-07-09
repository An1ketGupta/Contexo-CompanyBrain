import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const qs = req.nextUrl.search;
  return proxyJson(req, `/integrations/google-workspace/meet-folders/names${qs}`);
}
