import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

// Re-mints a signed upload URL for an existing failed/pending document row,
// so "Retry failed" in the upload queue reuses the same DB row instead of
// creating a duplicate.
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
  return proxyJson(req, "/documents/upload/refresh", {
    method: "POST",
    body: { doc_id: body.doc_id },
  });
}
