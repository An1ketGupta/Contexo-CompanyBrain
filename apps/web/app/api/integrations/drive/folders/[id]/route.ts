import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyJson(
    req,
    `/integrations/drive/folders/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}
