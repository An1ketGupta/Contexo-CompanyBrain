import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return proxyJson(request, `/generated-documents/${id}/reject`, {
    method: "POST",
    body,
  });
}
