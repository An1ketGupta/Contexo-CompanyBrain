import { NextRequest, NextResponse } from "next/server";
import { getAccessToken, unauthorized } from "@/lib/api-proxy";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const REQUEST_ID_HEADER = "x-request-id";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<Response> {
  const token = await getAccessToken();
  if (!token) return unauthorized();

  const requestId =
    req.headers.get(REQUEST_ID_HEADER) ?? crypto.randomUUID();

  const upstream = await fetch(`${API_URL}/compliance/admin/report.csv`, {
    headers: {
      Authorization: `Bearer ${token}`,
      [REQUEST_ID_HEADER]: requestId,
    },
    cache: "no-store",
  });

  if (!upstream.ok) {
    const body = await upstream.text().catch(() => "");
    return NextResponse.json(
      { code: "upstream_error", message: body.slice(0, 200) },
      { status: upstream.status },
    );
  }

  const blob = await upstream.arrayBuffer();
  const filename =
    upstream.headers.get("content-disposition") ??
    `attachment; filename="compliance-report.csv"`;

  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "text/csv",
      "Content-Disposition": filename,
      [REQUEST_ID_HEADER]: requestId,
    },
  });
}
