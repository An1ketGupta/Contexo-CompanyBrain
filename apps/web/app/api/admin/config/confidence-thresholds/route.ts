import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/admin/config/confidence-thresholds");
}

export async function PUT(req: NextRequest): Promise<Response> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { code: "bad_request", message: "Invalid JSON body." },
      { status: 400 },
    );
  }
  return proxyJson(req, "/admin/config/confidence-thresholds", {
    method: "PUT",
    body,
  });
}
