/**
 * Typed parsing for the backend's error envelope, plus a one-call helper that
 * turns it into a user-visible UX (toast, redirect, retry button).
 *
 * Envelope shape (mirrors apps/api/app/errors.py):
 *   { code, message, request_id?, retry_after?, details? }
 *
 * Why a centralized mapper instead of `toast.error(err.message)` everywhere:
 *   - 401 should sign the user out, not just show a toast.
 *   - 402 should suggest /pricing, not a generic "try again".
 *   - 429 should show a countdown, not a flash.
 *   - 5xx should surface the request_id so the user can quote it.
 * Spreading those rules across call sites is how you get bugs in the boring
 * paths that catch the worst customers off guard.
 */
import { toast } from "sonner";
import { REQUEST_ID_HEADER } from "./request-id";

export type ErrorCode =
  | "bad_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "payload_too_large"
  | "unsupported_media_type"
  | "validation_error"
  | "rate_limited"
  | "quota_exceeded"
  | "no_organization"
  | "moderation_blocked"   // V4 #79 — input matched a moderation pattern; render amber/shield UI
  | "internal_error"
  | "upstream_unavailable"
  | "service_unavailable"
  | "gateway_timeout"
  | "http_error"
  | "network_error"        // synthesized client-side when fetch itself fails
  | "stream_interrupted";  // synthesized when an SSE stream dies mid-flight

export interface ApiError {
  code: ErrorCode;
  message: string;
  status: number;
  request_id?: string;
  retry_after?: number;
  details?: Record<string, unknown>;
}

const FALLBACK_MESSAGE = "Something went wrong. Try again in a moment.";

interface RawEnvelope {
  code?: unknown;
  message?: unknown;
  request_id?: unknown;
  retry_after?: unknown;
  details?: unknown;
  // Older endpoints in this codebase still use { detail } / { error }; we
  // normalize them into the envelope so call sites only see one shape.
  detail?: unknown;
  error?: unknown;
}

function pickMessage(raw: RawEnvelope): string {
  if (typeof raw.message === "string" && raw.message) return raw.message;
  if (typeof raw.detail === "string" && raw.detail) return raw.detail;
  if (typeof raw.error === "string" && raw.error) return raw.error;
  return FALLBACK_MESSAGE;
}

function codeFromStatus(status: number): ErrorCode {
  switch (status) {
    case 400:
      return "bad_request";
    case 401:
      return "unauthorized";
    case 402:
      return "quota_exceeded";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 409:
      return "conflict";
    case 413:
      return "payload_too_large";
    case 415:
      return "unsupported_media_type";
    case 422:
      return "validation_error";
    case 429:
      return "rate_limited";
    case 500:
      return "internal_error";
    case 502:
      return "upstream_unavailable";
    case 503:
      return "service_unavailable";
    case 504:
      return "gateway_timeout";
    default:
      return "http_error";
  }
}

/**
 * Parse a fetch Response into our envelope. Tolerates non-JSON bodies (some
 * upstream proxies return HTML on 502) by falling back to the status code.
 */
export async function parseApiError(res: Response): Promise<ApiError> {
  const requestId = res.headers.get(REQUEST_ID_HEADER) ?? undefined;
  const retryAfterHeader = res.headers.get("retry-after");
  const retryAfterFromHeader = retryAfterHeader
    ? parseInt(retryAfterHeader, 10)
    : undefined;

  let raw: RawEnvelope = {};
  try {
    raw = (await res.json()) as RawEnvelope;
  } catch {
    // Non-JSON body — fall through with raw = {}
  }

  const code: ErrorCode =
    (typeof raw.code === "string" ? (raw.code as ErrorCode) : undefined) ??
    codeFromStatus(res.status);

  return {
    code,
    message: pickMessage(raw),
    status: res.status,
    request_id:
      typeof raw.request_id === "string" ? raw.request_id : requestId,
    retry_after:
      typeof raw.retry_after === "number"
        ? raw.retry_after
        : Number.isFinite(retryAfterFromHeader)
        ? retryAfterFromHeader
        : undefined,
    details:
      raw.details && typeof raw.details === "object"
        ? (raw.details as Record<string, unknown>)
        : undefined,
  };
}

export function networkError(err: unknown): ApiError {
  const message =
    err instanceof Error ? err.message : "Couldn't reach the server.";
  return {
    code: "network_error",
    message,
    status: 0,
  };
}

/**
 * Turn an ApiError into a side effect (toast / redirect / nothing). Returns
 * the error unchanged so callers can `throw` it after if they want.
 *
 * `silent: true` skips the toast — use when the calling component renders an
 * inline error UI (e.g., the chat bubble shows its own retry button).
 */
export function reportApiError(
  err: ApiError,
  options: { silent?: boolean; onSignOut?: () => void } = {},
): ApiError {
  // 401 — session is dead. Don't just toast; sign the user out.
  if (err.code === "unauthorized") {
    if (options.onSignOut) options.onSignOut();
    else if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    return err;
  }

  if (options.silent) return err;

  // 429 — show a countdown so users see something is happening, not a flash.
  if (err.code === "rate_limited" && err.retry_after) {
    toast.error(`Slow down — try again in ${err.retry_after}s.`, {
      description: err.message,
    });
    return err;
  }

  // 402 — over the monthly plan budget. Different copy from "slow down".
  if (err.code === "quota_exceeded") {
    toast.error("You've used this month's AI tasks.", {
      description: err.message,
    });
    return err;
  }

  // V4 #79 — moderation refusal. Warning tone, not destructive, no retry hint.
  if (err.code === "moderation_blocked") {
    toast.warning("Query blocked by content moderation.", {
      description: err.message,
    });
    return err;
  }

  // 5xx — surface the request id so users can quote it to support.
  if (err.status >= 500) {
    toast.error(err.message, {
      description: err.request_id ? `Reference: ${err.request_id}` : undefined,
    });
    return err;
  }

  // Default — plain message.
  toast.error(err.message);
  return err;
}
