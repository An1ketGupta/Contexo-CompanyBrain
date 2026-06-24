import { NextRequest } from "next/server";
import { proxyDownload } from "@/lib/api-proxy";

// Workspace GDPR data export — admin-only on the FastAPI side. Forwards
// the upstream ZIP stream verbatim. Rate limit is 1/day per workspace
// to bound the per-tenant DB cost of the export.
export async function GET(req: NextRequest): Promise<Response> {
  return proxyDownload(req, "/organizations/me/export");
}
