import { NextRequest } from "next/server";
import { proxyPostJson } from "@/lib/api-proxy";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPostJson(
    req,
    `/auth/invitations/${encodeURIComponent(token)}/accept-authenticated`,
  );
}
