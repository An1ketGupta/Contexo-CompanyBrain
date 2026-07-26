import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

// HR confirming or rejecting one proposed fill-point. PATCH rather than POST
// because it's a decision on an existing row, and there's no proxyPatchJson
// helper — so the body is read here and forwarded explicitly.
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; slotId: string }> },
) {
  const { id, slotId } = await params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { code: "bad_request", message: "Invalid JSON body." },
      { status: 400 },
    );
  }
  return proxyJson(request, `/onboarding/templates/${id}/slots/${slotId}`, {
    method: "PATCH",
    body,
  });
}
