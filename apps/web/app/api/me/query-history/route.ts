import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

/** V3 #91 — paginated user query history. Filters: cursor, limit, intent, search. */
export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const qs = url.searchParams.toString();
  const path = qs ? `/users/me/query-history?${qs}` : "/users/me/query-history";
  return proxyJson(req, path, { method: "GET" });
}
