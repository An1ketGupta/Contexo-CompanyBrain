import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ documentId: string }> },
): Promise<Response> {
  const { documentId } = await params;
  return proxyJson(req, `/compliance/${documentId}/dismiss`, {
    method: "POST",
    body: {},
  });
}
