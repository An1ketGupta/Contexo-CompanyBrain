import { NextRequest } from "next/server";
import { proxyJson, proxyMultipart } from "@/lib/api-proxy";

export async function GET(request: NextRequest) {
  const includeArchived = request.nextUrl.searchParams.get("include_archived");
  const query = includeArchived === "true" ? "?include_archived=true" : "";
  return proxyJson(request, `/document-templates${query}`);
}

// Multipart — creating a template and storing its first version is one step.
export async function POST(request: NextRequest) {
  return proxyMultipart(request, "/document-templates");
}
