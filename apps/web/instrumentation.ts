/**
 * Next.js 16 server-side instrumentation hook. Called once per server start
 * before the first request is handled. We use it to:
 *   1. Initialize Sentry for Node + Edge runtimes (the client config is
 *      bundled separately and runs in the browser).
 *   2. Forward request-rendering errors to Sentry via onRequestError so
 *      Server Component crashes show up alongside route-handler errors.
 *
 * The runtime check on NEXT_RUNTIME is the official Sentry pattern — it
 * keeps the Node SDK out of the Edge bundle and vice versa.
 */
import type { Instrumentation } from "next";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("./sentry.edge.config");
  }
}

// Server-side error capture for the App Router. Sentry's wizard wires this up
// automatically when using withSentryConfig; we declare it explicitly so the
// behavior is visible in source rather than buried in a plugin.
export const onRequestError: Instrumentation.onRequestError = async (
  err,
  request,
  context,
) => {
  const Sentry = await import("@sentry/nextjs");
  Sentry.captureRequestError(err, request, context);
};
