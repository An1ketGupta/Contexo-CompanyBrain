import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  let body: unknown = undefined;
  try {
    body = await req.json();
  } catch {
    /* allow empty body; the upstream will validate */
  }
  return proxyJson(req, `/admin/support/${encodeURIComponent(id)}/send`, {
    method: "POST",
    body,
  });
}
