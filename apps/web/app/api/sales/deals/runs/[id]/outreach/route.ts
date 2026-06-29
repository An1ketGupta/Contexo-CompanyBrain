import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ code: "bad_request", message: "Invalid JSON body." }, { status: 400 });
  }
  return proxyJson(request, `/sales/deals/runs/${id}/outreach`, { method: "PATCH", body });
}
