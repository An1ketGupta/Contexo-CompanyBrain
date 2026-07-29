import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ variableId: string }> },
) {
  const { variableId } = await params;
  const body = await request.json().catch(() => ({}));
  return proxyJson(request, `/document-templates/variables/${variableId}`, {
    method: "PATCH",
    body,
  });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ variableId: string }> },
) {
  const { variableId } = await params;
  return proxyJson(request, `/document-templates/variables/${variableId}`, {
    method: "DELETE",
  });
}
