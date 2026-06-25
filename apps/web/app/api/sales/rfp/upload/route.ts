/**
 * Multipart proxy for /sales/rfp/upload — bypasses proxyJson because we
 * forward the raw FormData. We stream the request body straight through
 * rather than buffering, so 25 MB uploads don't blow the Vercel function
 * memory limit.
 */
import { NextRequest, NextResponse } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<Response> {
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);

  const contentType = req.headers.get("content-type");
  if (!contentType || !contentType.includes("multipart/form-data")) {
    return NextResponse.json(
      { code: "bad_request", message: "Multipart form data required.", request_id: requestId },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }

  const upstream = await fetch(`${API_URL}/sales/rfp/upload`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      [REQUEST_ID_HEADER]: requestId,
      "Content-Type": contentType,
    },
    // @ts-expect-error — Node fetch supports body: ReadableStream, types lag.
    body: req.body,
    duplex: "half",
    cache: "no-store",
  });

  const data = await upstream.json().catch(() => ({}));
  const headers = new Headers();
  headers.set(REQUEST_ID_HEADER, upstream.headers.get(REQUEST_ID_HEADER) ?? requestId);
  return NextResponse.json(data, { status: upstream.status, headers });
}
