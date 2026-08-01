import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyPostJson(req, `/admin/sales/${encodeURIComponent(id)}/reply`);
}
