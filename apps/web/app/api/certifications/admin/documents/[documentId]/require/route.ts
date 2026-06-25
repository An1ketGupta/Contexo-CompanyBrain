import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ documentId: string }> },
): Promise<Response> {
  const { documentId } = await ctx.params;
  return proxyJson(req, `/certifications/admin/documents/${documentId}/require`);
}
