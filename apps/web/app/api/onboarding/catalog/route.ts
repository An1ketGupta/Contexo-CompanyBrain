import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/onboarding/catalog");
}

export async function POST(req: NextRequest): Promise<Response> {
  return proxyPostJson(req, "/onboarding/catalog/steps");
}
