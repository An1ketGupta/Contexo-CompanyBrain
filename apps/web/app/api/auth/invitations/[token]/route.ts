import { NextRequest, NextResponse } from "next/server";
import { API_URL } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

/**
 * Public invite lookup. Used by the accept-invite page to pre-fill the signup
 * form. No auth header — token in the URL IS the credential.
 */
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ token: string }> },
): Promise<Response> {
  const { token } = await params;
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));

  const upstream = await fetch(
    `${API_URL}/auth/invitations/${encodeURIComponent(token)}`,
    {
      headers: { [REQUEST_ID_HEADER]: requestId },
      cache: "no-store",
    },
  );

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { [REQUEST_ID_HEADER]: requestId },
  });
}
