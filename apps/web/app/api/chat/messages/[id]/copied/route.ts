import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/chat/messages/${encodeURIComponent(id)}/copied`, {
    method: "POST",
    body: {},
  });
}
