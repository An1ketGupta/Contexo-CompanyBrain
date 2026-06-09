import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

// Receives { filename, content_type, file_size } — no file body.
// Returns { doc_id, upload_url } — browser uploads directly to Supabase Storage.
export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json().catch(() => null);
  if (!body?.filename || !body?.content_type || body?.file_size == null) {
    const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
    return NextResponse.json(
      {
        code: "bad_request",
        message: "Missing filename, content_type, or file_size.",
        request_id: requestId,
      },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyJson(req, "/documents/upload/init", { method: "POST", body });
}
