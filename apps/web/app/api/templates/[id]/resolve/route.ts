import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(req, `/templates/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    body,
  });
}
