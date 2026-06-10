import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/email/address");
}

export async function POST(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/email/address", { method: "POST" });
}
