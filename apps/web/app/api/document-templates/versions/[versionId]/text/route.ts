import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ versionId: string }> },
) {
  const { versionId } = await params;
  return proxyJson(request, `/document-templates/versions/${versionId}/text`);
}
