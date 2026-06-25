import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ announcementId: string }> },
): Promise<Response> {
  const { announcementId } = await params;
  return proxyJson(
    req,
    `/admin/announcements/${encodeURIComponent(announcementId)}/cancel`,
    { method: "POST" },
  );
}
