import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/compliance/admin/config");
}

export async function PATCH(req: NextRequest): Promise<Response> {
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  return proxyJson(req, "/compliance/admin/config", {
    method: "PATCH",
    body,
  });
}
