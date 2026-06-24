import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  req: NextRequest,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await context.params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(
    req,
    `/organizations/members/${encodeURIComponent(id)}/role`,
    { method: "PATCH", body },
  );
}
