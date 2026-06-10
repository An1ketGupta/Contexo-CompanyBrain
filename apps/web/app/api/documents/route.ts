import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  // Forward the query string verbatim — filtering/sorting/pagination params
  // are all server-side on FastAPI. Keeping the proxy dumb means new filters
  // don't require Next-side changes.
  const qs = req.nextUrl.search;
  return proxyJson(req, `/documents${qs}`);
}
