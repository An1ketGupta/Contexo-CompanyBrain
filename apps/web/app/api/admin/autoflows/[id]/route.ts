import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

interface Params {
  params: Promise<{ id: string }>;
}

export async function GET(req: NextRequest, ctx: Params): Promise<Response> {
  const { id } = await ctx.params;
  return proxyJson(req, `/admin/autoflows/${encodeURIComponent(id)}`);
}

export async function PATCH(req: NextRequest, ctx: Params): Promise<Response> {
  const { id } = await ctx.params;
  const body = await req.json();
  return proxyJson(req, `/admin/autoflows/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body,
  });
}

export async function DELETE(req: NextRequest, ctx: Params): Promise<Response> {
  const { id } = await ctx.params;
  return proxyJson(req, `/admin/autoflows/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
