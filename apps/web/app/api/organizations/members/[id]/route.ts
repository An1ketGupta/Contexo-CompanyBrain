import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function DELETE(
  req: NextRequest,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await context.params;
  return proxyJson(
    req,
    `/organizations/members/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}
