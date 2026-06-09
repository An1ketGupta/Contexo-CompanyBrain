import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/chat/conversations/${encodeURIComponent(id)}`);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") {
    const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
    return NextResponse.json(
      { code: "bad_request", message: "Invalid body.", request_id: requestId },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyJson(req, `/chat/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/chat/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
