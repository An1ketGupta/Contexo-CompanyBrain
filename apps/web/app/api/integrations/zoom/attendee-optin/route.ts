import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/zoom/attendee-optin");
}

export async function PUT(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/zoom/attendee-optin", { method: "PUT" });
}

export async function DELETE(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/zoom/attendee-optin", { method: "DELETE" });
}
