import { NextRequest, NextResponse } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

/**
 * Proxy for GET /chat/conversations/{id}/export?format=markdown|json.
 *
 * The upstream response is an attachment (Content-Disposition: attachment),
 * so we pipe the body through as-is and forward both the Content-Type and
 * Content-Disposition headers. JSON / Markdown errors from FastAPI are
 * already in our envelope shape on the non-2xx path.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);

  const { id } = await params;
  const url = new URL(request.url);
  const format = url.searchParams.get("format") ?? "markdown";

  const upstream = await fetch(
    `${API_URL}/chat/conversations/${encodeURIComponent(id)}/export?format=${encodeURIComponent(
      format,
    )}`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
        [REQUEST_ID_HEADER]: requestId,
      },
      cache: "no-store",
    },
  );

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;

  if (!upstream.ok || !upstream.body) {
    const data = await upstream.json().catch(() => ({
      code: "upstream_unavailable",
      message: "Failed to export conversation.",
      request_id: outboundRequestId,
    }));
    return NextResponse.json(data, {
      status: upstream.status,
      headers: { [REQUEST_ID_HEADER]: outboundRequestId },
    });
  }

  const headers = new Headers();
  const ct = upstream.headers.get("content-type");
  if (ct) headers.set("Content-Type", ct);
  const cd = upstream.headers.get("content-disposition");
  if (cd) headers.set("Content-Disposition", cd);
  headers.set(REQUEST_ID_HEADER, outboundRequestId);

  return new Response(upstream.body, { status: 200, headers });
}
