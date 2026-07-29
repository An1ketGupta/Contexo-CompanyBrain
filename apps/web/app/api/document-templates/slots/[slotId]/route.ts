import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ slotId: string }> },
) {
  const { slotId } = await params;
  const body = await request.json().catch(() => ({}));
  return proxyJson(request, `/document-templates/slots/${slotId}`, {
    method: "PATCH",
    body,
  });
}
