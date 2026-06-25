import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ channelId: string }> },
): Promise<Response> {
  const { channelId } = await ctx.params;
  return proxyJson(req, `/channels/${channelId}`);
}
