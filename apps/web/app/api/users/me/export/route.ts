import { NextRequest } from "next/server";
import { proxyDownload } from "@/lib/api-proxy";

// Personal GDPR data export — forwards the upstream ZIP stream verbatim.
// See `apps/api/app/routers/settings.py::export_my_data` for the contents
// of the archive and the rate limit (3/day per user).
export async function GET(req: NextRequest): Promise<Response> {
  return proxyDownload(req, "/users/me/export");
}
