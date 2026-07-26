import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

// Renders the confirmed fill-points against sample data. Unlike the legacy
// apply-mappings flow this does not modify the stored template, so it's safe to
// call as often as HR wants while they iterate.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxyJson(request, `/onboarding/templates/${id}/render-preview`, {
    method: "POST",
  });
}
