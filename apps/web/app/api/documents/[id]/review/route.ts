import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const body = await req.json();
  return proxyJson(
    req,
    `/documents/${encodeURIComponent(id)}/review`,
    { method: "PATCH", body },
  );
}
