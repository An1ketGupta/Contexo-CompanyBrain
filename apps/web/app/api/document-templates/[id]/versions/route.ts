import { NextRequest } from "next/server";
import { proxyJson, proxyMultipart } from "@/lib/api-proxy";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxyJson(request, `/document-templates/${id}/versions`);
}

// Multipart — a new version. The previous file is left exactly where it is.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxyMultipart(request, `/document-templates/${id}/versions`);
}
