import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const body = await req.json().catch(() => null);
  return proxyJson(req, `/collections/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: body ?? {},
  });
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  return proxyJson(req, `/collections/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
