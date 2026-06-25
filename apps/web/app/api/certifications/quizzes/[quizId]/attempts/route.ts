import { NextRequest } from "next/server";
import { proxyJson } from "@/lib/api-proxy";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ quizId: string }> },
): Promise<Response> {
  const { quizId } = await ctx.params;
  return proxyJson(req, `/certifications/quizzes/${quizId}/attempts`);
}
