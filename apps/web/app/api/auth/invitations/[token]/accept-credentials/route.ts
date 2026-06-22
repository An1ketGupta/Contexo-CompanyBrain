import { NextRequest } from "next/server";
import { proxyPublicPostJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicPostJson(
    req,
    `/auth/invitations/${encodeURIComponent(token)}/accept-credentials`,
  );
}
