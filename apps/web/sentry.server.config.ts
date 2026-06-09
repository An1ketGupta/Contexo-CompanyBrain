/**
 * Server-side Sentry config — runs in the Node runtime (route handlers,
 * server components, middleware on Node). Loaded by instrumentation.ts.
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
    release: process.env.RELEASE_VERSION || undefined,
    tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE ?? "0.1"),
    sendDefaultPii: false,
    // The server runs requests for many users — we set user/org tags per-request
    // in the proxy helper, not globally.
  });
}
