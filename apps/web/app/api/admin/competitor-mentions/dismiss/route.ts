import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function POST(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/admin/competitor-mentions/dismiss");
}
