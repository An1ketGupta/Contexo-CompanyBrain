import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

export async function proxy(request: NextRequest) {
  // Mint or accept the per-request trace id. We inject it back into the
  // request headers so downstream route handlers can read it without
  // generating their own, and we set it on the eventual response so the
  // browser can capture it into Sentry breadcrumbs.
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  const forwardedHeaders = new Headers(request.headers);
  forwardedHeaders.set(REQUEST_ID_HEADER, requestId);

  let supabaseResponse = NextResponse.next({
    request: { headers: forwardedHeaders },
  });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({
            request: { headers: forwardedHeaders },
          });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { pathname } = request.nextUrl;

  // Every dashboard prefix that should require an authenticated session.
  // Keep this in sync with the (dashboard) route group; missing one means
  // the page falls back to the server-component check (slower, can flash).
  const protectedRoutes = [
    "/chat",
    "/documents",
    "/settings",
    "/admin",
    "/activity",
    "/approvals",
    "/archive",
    "/compliance",
    "/help",
    "/history",
    "/notifications",
  ];
  const isProtected = protectedRoutes.some(
    (r) => pathname === r || pathname.startsWith(`${r}/`),
  );

  // Redirect unauthenticated users away from protected routes. Carry
  // `redirectedFrom` so the login page can deep-link back after sign-in.
  if (!user && isProtected) {
    const target = new URL("/login", request.url);
    target.searchParams.set("redirectedFrom", pathname);
    const redirect = NextResponse.redirect(target);
    redirect.headers.set(REQUEST_ID_HEADER, requestId);
    return redirect;
  }

  // Note on /admin/*: the proxy only enforces "must be authenticated" here.
  // The admin role check lives in app/(dashboard)/admin/layout.tsx — a server
  // component that runs once per render rather than on every prefetch/nav
  // through the proxy. Keeping role logic out of the proxy avoids a Supabase
  // round-trip on every admin-area request.

  // Redirect authenticated users away from auth pages
  if (user && (pathname === "/login" || pathname === "/signup")) {
    const redirect = NextResponse.redirect(new URL("/chat", request.url));
    redirect.headers.set(REQUEST_ID_HEADER, requestId);
    return redirect;
  }

  supabaseResponse.headers.set(REQUEST_ID_HEADER, requestId);
  return supabaseResponse;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
