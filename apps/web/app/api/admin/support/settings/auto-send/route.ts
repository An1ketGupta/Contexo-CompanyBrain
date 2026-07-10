import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/admin/support/settings/auto-send");
}

export async function PUT(req: NextRequest): Promise<Response> {
  let body: unknown = undefined;
  try {
    body = await req.json();
  } catch {
    /* allow empty body; the upstream will validate */
  }
  return proxyJson(req, "/admin/support/settings/auto-send", {
    method: "PUT",
    body,
  });
}
