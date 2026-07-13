import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/zoom/transcript-optin");
}

export async function PUT(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/zoom/transcript-optin", { method: "PUT" });
}

export async function DELETE(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/zoom/transcript-optin", { method: "DELETE" });
}
