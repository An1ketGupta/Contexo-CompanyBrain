/**
 * Browser instrumentation. Runs before the app becomes interactive — keep
 * imports lean since anything pulled in here ships to every page.
 *
 * Two responsibilities:
 *   1. Initialize Sentry for the browser.
 *   2. Export onRouterTransitionStart so Sentry can stitch together
 *      client-side navigations as part of the same performance trace.
 */
import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    release: process.env.NEXT_PUBLIC_RELEASE_VERSION || undefined,
    tracesSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? "0.1",
    ),
    // Session replay is gated by an explicit env flag — it ships ~80KB and
    // can collect potentially-sensitive form data even with masking.
    replaysSessionSampleRate:
      process.env.NEXT_PUBLIC_SENTRY_REPLAYS === "1" ? 0.1 : 0,
    replaysOnErrorSampleRate:
      process.env.NEXT_PUBLIC_SENTRY_REPLAYS === "1" ? 1.0 : 0,
    integrations:
      process.env.NEXT_PUBLIC_SENTRY_REPLAYS === "1"
        ? [Sentry.replayIntegration({ maskAllText: true, blockAllMedia: true })]
        : [],
    // Drop noise that isn't actionable.
    ignoreErrors: [
      // Aborted SSE streams when the user stops or navigates away.
      "AbortError",
      "TypeError: Failed to fetch",
      "TypeError: NetworkError when attempting to fetch resource.",
      "TypeError: Load failed",
      // Browser extensions throwing inside event handlers we don't own.
      "ResizeObserver loop limit exceeded",
      "ResizeObserver loop completed with undelivered notifications.",
      "Non-Error promise rejection captured",
    ],
    sendDefaultPii: false,
  });
}

// Tells Sentry when a soft (client-side) navigation begins. Without this the
// performance trace for a route ends at the first page load and we lose the
// post-navigation timing.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
