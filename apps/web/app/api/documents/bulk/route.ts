import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { coerceRequestId, REQUEST_ID_HEADER } from "@/lib/request-id";

export async function DELETE(req: NextRequest): Promise<Response> {
  const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      {
        code: "bad_request",
        message: "Invalid JSON body.",
        request_id: requestId,
      },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyJson(req, "/documents/bulk", { method: "DELETE", body });
}
