import { NextRequest, NextResponse } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

/**
 * Proxies POST /api/chat/stream → FastAPI /chat/stream, preserving the SSE
 * body as a passthrough ReadableStream so the browser sees tokens land as
 * they're produced (no buffering at the Next.js layer).
 *
 * We DO NOT call .json() / .text() on the upstream response on the happy
 * path — that would collapse the stream. Instead we pipe upstream.body
 * straight through. On the error path (rate limit, validation, server
 * down) we DO read the JSON envelope so the browser receives our standard
 * error shape rather than an empty stream.
 */
export async function POST(request: NextRequest): Promise<Response> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));

  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);

  const payload = await request.json().catch(() => null);
  if (!payload || typeof payload !== "object") {
    return NextResponse.json(
      {
        code: "bad_request",
        message: "Invalid request body.",
        request_id: requestId,
      },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }

  const upstream = await fetch(`${API_URL}/chat/stream`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      [REQUEST_ID_HEADER]: requestId,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
    // Forward the client abort to FastAPI so it can stop generation.
    signal: request.signal,
    cache: "no-store",
  });

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;

  // Non-stream upstream response (rate limit, auth, validation) — mirror it
  // back as JSON so the browser sees our envelope shape.
  if (!upstream.ok || !upstream.body) {
    const data = await upstream.json().catch(() => ({
      code: "upstream_unavailable",
      message: "The chat service is unavailable.",
      request_id: outboundRequestId,
    }));
    const headers = new Headers();
    headers.set(REQUEST_ID_HEADER, outboundRequestId);
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers.set("retry-after", retryAfter);
    return NextResponse.json(data, {
      status: upstream.status,
      headers,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // Disable buffering on Vercel/Nginx so SSE flushes immediately.
      "X-Accel-Buffering": "no",
      [REQUEST_ID_HEADER]: outboundRequestId,
    },
  });
}
