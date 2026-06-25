import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const include = url.searchParams.get("include_archived");
  const path = include
    ? `/org-personas?include_archived=${include}`
    : "/org-personas";
  return proxyJson(req, path);
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, "/org-personas", { method: "POST", body });
}
