import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await context.params;
  return proxyJson(
    req,
    `/documents/${encodeURIComponent(id)}/reprocess`,
    { method: "POST" },
  );
}
