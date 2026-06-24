import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface Params {
  params: Promise<{ id: string }>;
}

export async function DELETE(req: NextRequest, ctx: Params): Promise<Response> {
  const { id } = await ctx.params;
  return proxyJson(req, `/admin/duplicates/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
