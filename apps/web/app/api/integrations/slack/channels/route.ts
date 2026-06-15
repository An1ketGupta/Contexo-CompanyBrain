import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const refresh = url.searchParams.get("refresh") === "true";
  const path = refresh
    ? "/integrations/slack/channels?refresh=true"
    : "/integrations/slack/channels";
  return proxyJson(req, path);
}
