import { NextRequest, NextResponse } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

/**
 * Proxies POST /api/chat/messages/{id}/regenerate to the FastAPI SSE
 * surface. Mirrors `/api/chat/stream` — the only differences are the path
 * and that the request body is optional ({ refinement?: string }).
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);
  const { id } = await params;

  const payload = await request.json().catch(() => ({}));

  const upstream = await fetch(
    `${API_URL}/chat/messages/${encodeURIComponent(id)}/regenerate`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        [REQUEST_ID_HEADER]: requestId,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(payload ?? {}),
      signal: request.signal,
      cache: "no-store",
    },
  );

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;

  if (!upstream.ok || !upstream.body) {
    const data = await upstream.json().catch(() => ({
      code: "upstream_unavailable",
      message: "Failed to regenerate.",
      request_id: outboundRequestId,
    }));
    const headers = new Headers();
    headers.set(REQUEST_ID_HEADER, outboundRequestId);
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers.set("retry-after", retryAfter);
    return NextResponse.json(data, { status: upstream.status, headers });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
      [REQUEST_ID_HEADER]: outboundRequestId,
    },
  });
}
