import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

function badRequest(req: NextRequest): NextResponse {
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
  return NextResponse.json(
    { code: "bad_request", message: "Invalid body.", request_id: requestId },
    { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
  );
}

export async function PATCH(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") return badRequest(req);
  return proxyJson(req, "/users/me", { method: "PATCH", body });
}

export async function DELETE(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") return badRequest(req);
  return proxyJson(req, "/users/me", { method: "DELETE", body });
}
