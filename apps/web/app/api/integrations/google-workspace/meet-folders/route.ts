import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json();
  return proxyJson(req, "/integrations/google-workspace/meet-folders", {
    method: "POST",
    body,
  });
}
