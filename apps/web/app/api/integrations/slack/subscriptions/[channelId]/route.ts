import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ channelId: string }> },
): Promise<Response> {
  const { channelId } = await ctx.params;
  return proxyJson(
    req,
    `/integrations/slack/subscriptions/${encodeURIComponent(channelId)}`,
    { method: "DELETE" },
  );
}
