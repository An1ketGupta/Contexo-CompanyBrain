import { NextRequest, NextResponse } from "next/server";
import { API_URL } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

export async function POST(req: NextRequest): Promise<Response> {
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { code: "bad_request", message: "Invalid JSON.", request_id: requestId },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }

  // Forward IP + UA so the resolution audit on FastAPI reflects the actual
  // browser, not the Next.js server.
  const fwd = req.headers.get("x-forwarded-for") || "";
  const ua = req.headers.get("user-agent") || "";

  const upstream = await fetch(`${API_URL}/approvals/resolve-by-token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [REQUEST_ID_HEADER]: requestId,
      ...(fwd ? { "x-forwarded-for": fwd } : {}),
      ...(ua ? { "user-agent": ua } : {}),
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { [REQUEST_ID_HEADER]: outboundRequestId },
  });
}
