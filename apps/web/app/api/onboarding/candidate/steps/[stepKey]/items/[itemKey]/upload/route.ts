import { NextRequest } from "next/server";
import { proxyMultipart } from "@/lib/api-proxy";

type Params = { params: Promise<{ stepKey: string; itemKey: string }> };

export async function POST(req: NextRequest, { params }: Params): Promise<Response> {
  const { stepKey, itemKey } = await params;
  return proxyMultipart(
    req,
    `/onboarding/candidate/steps/${encodeURIComponent(stepKey)}` +
      `/items/${encodeURIComponent(itemKey)}/upload`,
  );
}
