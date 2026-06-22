import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

const SAFE_NEXT_DEFAULT = "/chat";

function safeNext(raw: string | null): string {
  // Only allow same-origin path redirects. Anything else (absolute URL,
  // protocol-relative, missing leading slash) falls back to the default to
  // prevent open-redirect via a crafted /auth/callback?next=https://evil.
  if (!raw) return SAFE_NEXT_DEFAULT;
  if (!raw.startsWith("/") || raw.startsWith("//")) return SAFE_NEXT_DEFAULT;
  return raw;
}

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  const next = safeNext(searchParams.get("next"));

  // Surface provider-side errors with a clean redirect instead of a confusing
  // /chat load where the user appears signed out.
  const providerError =
    searchParams.get("error_description") ||
    searchParams.get("error");
  if (providerError) {
    console.error("OAuth provider error:", providerError);
    return NextResponse.redirect(`${origin}/login?error=oauth_failed`);
  }

  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=missing_code`);
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);

  if (error) {
    console.error("OAuth callback exchange failed:", error.message);
    return NextResponse.redirect(`${origin}/login?error=exchange_failed`);
  }

  return NextResponse.redirect(`${origin}${next}`);
}
