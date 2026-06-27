import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/recruiting/notion-parent");
}

export async function POST(req: NextRequest): Promise<Response> {
  return proxyPostJson(req, "/recruiting/notion-parent");
}

export async function DELETE(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/recruiting/notion-parent", { method: "DELETE" });
}
