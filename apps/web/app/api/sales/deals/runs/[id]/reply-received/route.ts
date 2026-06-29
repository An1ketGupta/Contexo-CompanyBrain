import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    // empty body is fine
  }
  return proxyJson(request, `/sales/deals/runs/${id}/reply-received`, { method: "POST", body });
}
