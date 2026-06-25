import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ reportId: string }> },
): Promise<Response> {
  const { reportId } = await params;
  return proxyJson(req, `/admin/knowledge-health/reports/${encodeURIComponent(reportId)}`);
}
