import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

type Params = { params: Promise<{ id: string; stepKey: string }> };

// HR accepting or rejecting what the candidate did at one step. Unlike the
// per-submission review next door, this one moves the run.
export async function POST(req: NextRequest, { params }: Params): Promise<Response> {
  const { id, stepKey } = await params;
  return proxyPostJson(
    req,
    `/onboarding/runs/${encodeURIComponent(id)}` +
      `/steps/${encodeURIComponent(stepKey)}/review`,
  );
}
