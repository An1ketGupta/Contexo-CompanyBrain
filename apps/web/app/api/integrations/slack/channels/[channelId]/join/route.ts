import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ channelId: string }> },
): Promise<Response> {
  const { channelId } = await params;
  return proxyJson(
    req,
    `/integrations/slack/channels/${encodeURIComponent(channelId)}/join`,
    { method: "POST" },
  );
}
