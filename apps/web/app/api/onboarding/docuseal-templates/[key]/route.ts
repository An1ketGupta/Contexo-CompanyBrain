import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ key: string }> },
) {
  const { key } = await params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { code: "bad_request", message: "Invalid JSON body." },
      { status: 400 },
    );
  }
  return proxyJson(request, `/onboarding/docuseal-templates/${key}`, {
    method: "PUT",
    body,
  });
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ key: string }> },
) {
  const { key } = await params;
  return proxyJson(request, `/onboarding/docuseal-templates/${key}`, {
    method: "DELETE",
  });
}
