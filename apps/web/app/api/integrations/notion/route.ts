import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function DELETE(req: NextRequest): Promise<Response> {
  return proxyJson(req, "/integrations/notion", { method: "DELETE" });
}
