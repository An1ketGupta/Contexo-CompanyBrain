/**
 * Server-side helpers for Next.js API proxy routes that forward to FastAPI.
 *
 * Lifecycle:
 *   1. Browser hits /api/* in Next.js with the Supabase auth cookie.
 *   2. Proxy reads the access_token from that cookie (server-side).
 *   3. Proxy mints an X-Request-ID if the inbound request didn't carry one,
 *      and forwards it to FastAPI as `X-Request-ID`.
 *   4. FastAPI verifies the JWT against Supabase Auth and threads the ID
 *      through its structured log + Sentry scope.
 *   5. Response echoes the ID back to the browser so the UI can capture it
 *      into Sentry breadcrumbs and quote it in support emails.
 *
 * This keeps `NEXT_PUBLIC_API_URL` out of the browser AND gives us one place
 * to instrument errors, layer CDN rules, or swap transports later.
 */
import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { REQUEST_ID_HEADER, coerceRequestId } from "@/lib/request-id";

export const API_URL = process.env.API_URL ?? "http://localhost:8000";

export async function getAccessToken(): Promise<string | null> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

/**
 * Standard 401 response in our envelope shape so the frontend's typed parser
 * sees a consistent body whether the failure was here or in FastAPI.
 */
export function unauthorized(requestId?: string): NextResponse {
  const headers: HeadersInit = {};
  if (requestId) headers[REQUEST_ID_HEADER] = requestId;
  return NextResponse.json(
    {
      code: "unauthorized",
      message: "Your session has expired. Sign in again.",
      request_id: requestId,
    },
    { status: 401, headers },
  );
}

interface ProxyJsonOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Forward a JSON request to FastAPI. Mirrors the upstream status + body and
 * threads X-Request-ID in both directions. Use for everything except SSE.
 */
export async function proxyJson(
  request: NextRequest | Request,
  path: string,
  options: ProxyJsonOptions = {},
): Promise<NextResponse> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));

  const token = await getAccessToken();
  if (!token) return unauthorized(requestId);

  const { method = "GET", body, signal } = options;

  const upstream = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      [REQUEST_ID_HEADER]: requestId,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
    cache: "no-store",
  });

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;
  const retryAfter = upstream.headers.get("retry-after");

  if (upstream.status === 204) {
    const headers = new Headers();
    headers.set(REQUEST_ID_HEADER, outboundRequestId);
    if (retryAfter) headers.set("retry-after", retryAfter);
    return new NextResponse(null, { status: 204, headers });
  }

  const data = await upstream.json().catch(() => ({}));
  const headers = new Headers();
  headers.set(REQUEST_ID_HEADER, outboundRequestId);
  if (retryAfter) headers.set("retry-after", retryAfter);

  return NextResponse.json(data, { status: upstream.status, headers });
}

/**
 * Convenience for the very common shape: read JSON body off the inbound
 * request and POST it upstream. Returns the standard error envelope on bad
 * input rather than letting `request.json()` throw.
 */
export async function proxyPostJson(
  request: NextRequest,
  path: string,
): Promise<NextResponse> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      {
        code: "bad_request",
        message: "Invalid JSON body.",
        request_id: requestId,
      },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyJson(request, path, { method: "POST", body });
}

/**
 * Public (no-auth) variant of proxyJson. Used by endpoints where the
 * credential lives in the URL itself (e.g., invitation token) or where
 * the endpoint is genuinely public (e.g., /pricing). Skips the
 * getAccessToken / 401 short-circuit that proxyJson runs.
 */
export async function proxyPublicJson(
  request: NextRequest | Request,
  path: string,
  options: { method?: "GET" | "POST"; body?: unknown } = {},
): Promise<NextResponse> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  const { method = "GET", body } = options;

  const upstream = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      [REQUEST_ID_HEADER]: requestId,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });

  const outboundRequestId =
    upstream.headers.get(REQUEST_ID_HEADER) ?? requestId;
  const data = await upstream.json().catch(() => ({}));
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { [REQUEST_ID_HEADER]: outboundRequestId },
  });
}

/**
 * As `proxyPostJson`, but no bearer forwarded. For token-credentialed
 * unauthenticated POSTs like invite acceptance.
 */
export async function proxyPublicPostJson(
  request: NextRequest,
  path: string,
): Promise<NextResponse> {
  const requestId = coerceRequestId(request.headers.get(REQUEST_ID_HEADER));
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      {
        code: "bad_request",
        message: "Invalid JSON body.",
        request_id: requestId,
      },
      { status: 400, headers: { [REQUEST_ID_HEADER]: requestId } },
    );
  }
  return proxyPublicJson(request, path, { method: "POST", body });
}
