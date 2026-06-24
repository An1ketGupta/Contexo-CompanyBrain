import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface Params {
  params: Promise<{ id: string }>;
}

export async function GET(req: NextRequest, ctx: Params): Promise<Response> {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const qs = url.searchParams.toString();
  return proxyJson(
    req,
    `/admin/autoflows/${encodeURIComponent(id)}/runs${qs ? `?${qs}` : ""}`,
  );
}
