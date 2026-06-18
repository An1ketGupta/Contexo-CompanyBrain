import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/slack/subscriptions");
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await req.json();
  return proxyJson(req, "/integrations/slack/subscriptions", {
    method: "POST",
    body,
  });
}
