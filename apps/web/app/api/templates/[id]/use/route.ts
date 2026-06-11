import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface Params {
  params: Promise<{ id: string }>;
}

export async function POST(req: NextRequest, ctx: Params): Promise<Response> {
  const { id } = await ctx.params;
  return proxyJson(req, `/templates/${encodeURIComponent(id)}/use`, {
    method: "POST",
  });
}
