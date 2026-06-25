import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ sequenceId: string; stepId: string }> },
): Promise<Response> {
  const { sequenceId, stepId } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(
    req,
    `/sequences/${encodeURIComponent(sequenceId)}/steps/${encodeURIComponent(stepId)}`,
    { method: "PATCH", body },
  );
}
