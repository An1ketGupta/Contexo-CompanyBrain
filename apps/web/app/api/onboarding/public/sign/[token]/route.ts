import { NextRequest } from "next/server";
import { ESIGN_API_URL, proxyPublicJson } from "@/lib/api-proxy";

// Public signing prefill proxy — no auth (the token in the URL is the
// credential). Points at apps/esign (the Documenso adapter, a separate deploy
// from apps/api), which returns the Documenso embed host + recipient token the
// /sign page renders. Signature capture happens inside Documenso's embedded
// signer and is finalised via the /webhooks/documenso receiver — so there's no
// POST submit here anymore.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  return proxyPublicJson(request, `/sign/${token}`, { baseUrl: ESIGN_API_URL });
}
