import { NextRequest } from "next/server";
import { proxyJson, proxyPostJson } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  return proxyJson(request, "/document-templates/types");
}

export async function POST(request: NextRequest) {
  return proxyPostJson(request, "/document-templates/types");
}
