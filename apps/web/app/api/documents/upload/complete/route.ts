import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

// Called after the browser has finished uploading to Supabase Storage.
// Triggers async processing via Inngest.
export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => null);
  if (!body?.doc_id) {
    const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
    return NextResponse.json(
      {
        code: "bad_request",
        message: "Missing doc_id.",
        request_id: requestId,
      },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyJson(req, "/documents/upload/complete", {
    method: "POST",
    body: { doc_id: body.doc_id },
  });
}
