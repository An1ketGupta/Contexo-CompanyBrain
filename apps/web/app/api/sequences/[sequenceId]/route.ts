import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ sequenceId: string }> },
): Promise<Response> {
  const { sequenceId } = await params;
  return proxyJson(req, `/sequences/${encodeURIComponent(sequenceId)}`);
}
