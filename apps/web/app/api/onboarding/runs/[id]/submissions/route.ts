import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

type Params = { params: Promise<{ id: string }> };

export async function GET(req: NextRequest, { params }: Params): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/onboarding/runs/${encodeURIComponent(id)}/submissions`);
}
