import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyJson(
    req,
    `/documents/${encodeURIComponent(id)}/mark-reviewed`,
    { method: "POST" },
  );
}
