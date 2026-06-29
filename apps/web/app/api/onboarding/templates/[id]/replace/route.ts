import { NextRequest, NextResponse } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

// Multipart upload — replace a template's .docx bytes during the edit loop.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);

  const form = await request.formData();
  const upstream = await fetch(
    `${API_URL}/onboarding/templates/${id}/replace`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        [REQUEST_ID_HEADER]: requestId,
      },
      body: form,
      cache: "no-store",
    },
  );

  const outboundId = upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { [REQUEST_ID_HEADER]: outboundId },
  });
}
