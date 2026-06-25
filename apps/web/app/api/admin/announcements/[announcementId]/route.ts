import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ announcementId: string }> },
): Promise<Response> {
  const { announcementId } = await params;
  return proxyJson(req, `/admin/announcements/${encodeURIComponent(announcementId)}`);
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ announcementId: string }> },
): Promise<Response> {
  const { announcementId } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(
    req,
    `/admin/announcements/${encodeURIComponent(announcementId)}`,
    { method: "PATCH", body },
  );
}
