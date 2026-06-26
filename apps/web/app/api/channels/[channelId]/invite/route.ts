import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ channelId: string }> },
): Promise<Response> {
  const { channelId } = await ctx.params;
  return proxyPostJson(req, `/channels/${channelId}/invite`);
}
