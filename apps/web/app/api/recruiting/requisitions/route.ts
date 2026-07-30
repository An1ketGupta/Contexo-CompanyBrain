import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const archived = req.nextUrl.searchParams.get("archived") === "true";
  return proxyJson(
    req,
    archived ? "/recruiting/requisitions?archived=true" : "/recruiting/requisitions",
  );
}
