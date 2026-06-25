/**
 * Streams the generated .docx from FastAPI back to the browser. We can't use
 * proxyJson because it forces a JSON parse; the upstream sends an Office
 * Open XML binary.
 */
import { NextRequest } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

export const runtime = "nodejs";

interface RouteCtx { params: Promise<{ id: string }> }

export async function GET(req: NextRequest, { params }: RouteCtx): Promise<Response> {
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);
  const { id } = await params;

  const upstream = await fetch(`${API_URL}/sales/rfp/${id}/download`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      [REQUEST_ID_HEADER]: requestId,
    },
    cache: "no-store",
  });

  // Pass status/headers/body through verbatim so 410/409 from upstream still
  // reach the browser as expected.
  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
