import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => ({}));
  return proxyJson(
    req,
    `/documents/${encodeURIComponent(id)}/versions/init`,
    { method: "POST", body },
  );
}
