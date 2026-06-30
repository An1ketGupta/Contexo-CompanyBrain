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

  const url = new URL(req.url);
  const kind = url.searchParams.get("kind") ?? "summary";
  const upstream = await fetch(`${API_URL}/sales/rfp/${id}/download?kind=${kind}`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      [REQUEST_ID_HEADER]: requestId,
    },
    cache: "no-store",
    // Don't follow the redirect — let the browser hop directly to the signed
    // Supabase Storage URL so our Authorization header isn't sent to Storage.
    redirect: "manual",
  });

  if (upstream.status === 307 || upstream.status === 308) {
    const location = upstream.headers.get("location");
    if (location) {
      return Response.redirect(location, upstream.status);
    }
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
}
