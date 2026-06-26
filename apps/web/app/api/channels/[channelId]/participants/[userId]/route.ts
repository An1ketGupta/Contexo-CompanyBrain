import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ channelId: string; userId: string }> },
): Promise<Response> {
  const { channelId, userId } = await ctx.params;
  return proxyJson(req, `/channels/${channelId}/participants/${userId}`, {
    method: "DELETE",
  });
}
