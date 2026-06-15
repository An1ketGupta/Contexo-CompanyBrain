import { NextRequest, NextResponse } from "next/server";
import { API_URL } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

// Magic-link approval lookup is gated by the signed token itself, not by a
// Supabase session. We forward the request to FastAPI with no Authorization
// header — FastAPI's /approvals/by-token endpoint is public and validates
// against sha256(secret || token).

export async function GET(req: NextRequest): Promise<Response> {
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
  const token = req.nextUrl.searchParams.get("token") ?? "";
  if (!token) {
    return NextResponse.json(
      { code: "bad_request", message: "Missing token.", request_id: requestId },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }

  const upstream = await fetch(
    `${API_URL}/approvals/by-token?token=${encodeURIComponent(token)}`,
    {
      method: "GET",
      headers: { [REQUEST_ID_HEADER]: requestId },
      cache: "no-store",
    },
  );

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { [REQUEST_ID_HEADER]: outboundRequestId },
  });
}
