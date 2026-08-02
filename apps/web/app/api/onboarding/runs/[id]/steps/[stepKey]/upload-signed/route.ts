import { NextRequest, NextResponse } from "next/server";
import { API_URL, getAccessToken, unauthorized } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

// Multipart upload — bypass the JSON proxy and stream the form data through
// with the Authorization header attached.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; stepKey: string }> },
) {
  const { id, stepKey } = await params;
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);

  const form = await request.formData();
  const upstream = await fetch(
    `${API_URL}/onboarding/runs/${id}/steps/${encodeURIComponent(stepKey)}/upload-signed`,
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
