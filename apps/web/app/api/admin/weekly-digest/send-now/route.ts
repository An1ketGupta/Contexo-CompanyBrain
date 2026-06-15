import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/admin/weekly-digest/send-now", {
    method: "POST",
    body: {},
  });
}
