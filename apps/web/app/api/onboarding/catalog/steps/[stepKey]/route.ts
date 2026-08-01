import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

type Params = { params: Promise<{ stepKey: string }> };

export async function PATCH(req: NextRequest, { params }: Params): Promise<Response> {
  const { stepKey } = await params;
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") {
    const requestId = coerceRequestId(req.headers.get(REQUEST_ID_HEADER));
    return NextResponse.json(
      { code: "bad_request", message: "Invalid body.", request_id: requestId },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyJson(
    req,
    `/onboarding/catalog/steps/${encodeURIComponent(stepKey)}`,
    { method: "PATCH", body },
  );
}

export async function DELETE(req: NextRequest, { params }: Params): Promise<Response> {
  const { stepKey } = await params;
  return proxyJson(
    req,
    `/onboarding/catalog/steps/${encodeURIComponent(stepKey)}`,
    { method: "DELETE" },
  );
}
