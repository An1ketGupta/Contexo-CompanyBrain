import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(req: NextRequest): Promise<Response> {
  return proxyPostJson(req, "/integrations/dropbox/resources");
}
