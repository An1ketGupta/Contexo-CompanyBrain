import { NextRequest } from "next/server";
import { proxyPublicMultipart } from "@/lib/api-proxy";

// One file against one checklist item, authorised by the token in the path.
export async function POST(
  request: NextRequest,
  {
    params,
  }: { params: Promise<{ token: string; stepKey: string; itemKey: string }> },
) {
  const { token, stepKey, itemKey } = await params;
  return proxyPublicMultipart(
    request,
    `/onboarding/public/documents/${token}` +
      `/steps/${encodeURIComponent(stepKey)}` +
      `/items/${encodeURIComponent(itemKey)}`,
  );
}
