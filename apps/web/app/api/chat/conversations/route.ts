import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  // Forward the query string verbatim so `?q=` lands on FastAPI.
  const qs = req.nextUrl.search;
  return proxyJson(req, `/chat/conversations${qs}`);
}
