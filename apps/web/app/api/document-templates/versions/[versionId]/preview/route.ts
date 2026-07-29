import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ versionId: string }> },
) {
  const { versionId } = await params;
  const body = await request.json().catch(() => ({ values: {} }));
  return proxyJson(request, `/document-templates/versions/${versionId}/preview`, {
    method: "POST",
    body,
  });
}
