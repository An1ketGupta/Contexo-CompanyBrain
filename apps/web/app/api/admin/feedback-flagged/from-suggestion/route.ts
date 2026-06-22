import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(req: NextRequest): Promise<Response> {
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  return proxyJson(req, "/admin/feedback-flagged/from-suggestion", {
    method: "POST",
    body,
  });
}
