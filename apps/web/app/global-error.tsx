"use client";

/**
 * Last-resort error boundary — replaces the entire app shell (including the
 * root layout) when something crashes that the segment-level error.tsx files
 * couldn't catch. Must define its own <html>/<body>, no shared styling.
 *
 * Captures to Sentry directly because the React tree above this boundary is
 * already in a broken state, so we can't rely on any of our own helpers.
 */
import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
          background: "#fafafa",
          color: "#0a0a0a",
        }}
      >
        <div style={{ maxWidth: 420, padding: 24, textAlign: "center" }}>
          <div style={{ fontSize: 12, color: "#737373", letterSpacing: 1 }}>
            COMPANY BRAIN
          </div>
          <h1
            style={{
              fontSize: 22,
              fontWeight: 600,
              margin: "16px 0 8px",
            }}
          >
            Something broke completely.
          </h1>
          <p style={{ fontSize: 14, color: "#525252", margin: "0 0 20px" }}>
            We couldn&apos;t recover from this one. Reloading usually clears it.
            {error.digest && (
              <>
                <br />
                <span style={{ fontSize: 12, color: "#a3a3a3" }}>
                  Reference: {error.digest}
                </span>
              </>
            )}
          </p>
          <button
            onClick={() => unstable_retry()}
            style={{
              padding: "10px 20px",
              borderRadius: 6,
              border: "none",
              background: "#0a0a0a",
              color: "white",
              fontSize: 14,
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
