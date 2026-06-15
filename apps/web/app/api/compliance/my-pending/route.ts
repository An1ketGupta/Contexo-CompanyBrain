import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/compliance/my-pending");
}
