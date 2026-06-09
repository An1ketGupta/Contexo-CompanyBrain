/**
 * Single source of truth for X-Request-ID generation and the header name.
 *
 * The ID flows:  browser → Next.js proxy → FastAPI → structured log → Sentry tag
 * Every link in that chain echoes it back on the response, so a button-click in
 * the UI maps to one log line on the backend without grep gymnastics.
 *
 * Format: `req_<24 hex chars>`. Short enough to quote in a support email,
 * long enough to be globally unique, and matches the regex on the backend
 * (`^[A-Za-z0-9_\\-]{8,128}$`). Anything outside that shape gets replaced
 * server-side, so we keep the format here strict.
 */
export const REQUEST_ID_HEADER = "x-request-id";

/**
 * Web-Crypto-backed UUID where available (browser, edge, modern Node),
 * with a non-cryptographic fallback so this file doesn't throw under
 * test environments that lack crypto.
 */
function uuidLikeHex(length: number): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "").slice(0, length);
  }
  let out = "";
  while (out.length < length) {
    out += Math.random().toString(16).slice(2);
  }
  return out.slice(0, length);
}

export function newRequestId(): string {
  return `req_${uuidLikeHex(24)}`;
}

const SAFE = /^[A-Za-z0-9_\-]{8,128}$/;

export function coerceRequestId(value: string | null | undefined): string {
  return value && SAFE.test(value) ? value : newRequestId();
}
