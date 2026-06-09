import { NextRequest, NextResponse } from "next/server";
import { API_URL } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

/**
 * Bind a freshly-signed-up auth user to the invite's org. Unauthenticated —
 * the bearer is the token itself, plus an email-match check on the FastAPI
 * side. We deliberately don't forward the Supabase cookie: a flapping/cached
 * session from a previous account would only confuse the bind.
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ token: string }> },
): Promise<Response> {
  const { token } = await params;
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));

  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") {
    return NextResponse.json(
      { code: "bad_request", message: "Invalid body.", request_id: requestId },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }

  const upstream = await fetch(
    `${API_URL}/auth/invitations/${encodeURIComponent(token)}/accept`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [REQUEST_ID_HEADER]: requestId,
      },
      body: JSON.stringify(body),
      cache: "no-store",
    },
  );

  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { [REQUEST_ID_HEADER]: requestId },
  });
}
