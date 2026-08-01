import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

type Params = { params: Promise<{ id: string; submissionId: string }> };

export async function POST(req: NextRequest, { params }: Params): Promise<Response> {
  const { id, submissionId } = await params;
  return proxyPostJson(
    req,
    `/onboarding/runs/${encodeURIComponent(id)}` +
      `/submissions/${encodeURIComponent(submissionId)}/review`,
  );
}
