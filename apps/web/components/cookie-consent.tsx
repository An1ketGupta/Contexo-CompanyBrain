"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

// localStorage key. The trailing version suffix lets us invalidate stored
// consent (forcing the banner to re-show) if our processor list changes
// in a way that materially alters what users would consent to.
const CONSENT_KEY = "cb.consent.v1";

interface ConsentState {
  analytics: boolean;
  accepted_at: string;
}

function readConsent(): ConsentState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(CONSENT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ConsentState>;
    if (typeof parsed.accepted_at !== "string") return null;
    return {
      analytics: parsed.analytics === true,
      accepted_at: parsed.accepted_at,
    };
  } catch {
    return null;
  }
}

function writeConsent(state: ConsentState): void {
  try {
    window.localStorage.setItem(CONSENT_KEY, JSON.stringify(state));
    // Broadcast so any future analytics module can subscribe without
    // having to poll localStorage itself.
    window.dispatchEvent(
      new CustomEvent("cb:consent-updated", { detail: state }),
    );
  } catch {
    // Storage may be unavailable (Safari private mode etc.) — fail silently;
    // the banner will simply re-appear on next page load.
  }
}

/**
 * Bottom-of-viewport consent banner. Two real choices (Accept all /
 * Essential only) so the consent it captures is meaningful — single-button
 * "Accept" banners don't constitute lawful consent under GDPR.
 *
 * Renders nothing until a client-side mount completes, to avoid hydration
 * mismatch with the SSR-rendered tree (the banner's visibility depends on
 * localStorage, which doesn't exist server-side).
 */
export function CookieConsent() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const stored = readConsent();
    if (!stored) {
      setVisible(true);
    }
  }, []);

  if (!visible) return null;

  const decide = (analytics: boolean) => {
    writeConsent({ analytics, accepted_at: new Date().toISOString() });
    setVisible(false);
  };

  return (
    <div
      role="region"
      aria-label="Cookie consent"
      className="fixed inset-x-2 bottom-2 z-50 mx-auto max-w-3xl rounded-lg border border-border bg-background/95 p-4 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-background/80 sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-xs text-muted-foreground sm:max-w-[60%]">
          <p className="font-medium text-foreground">We use cookies</p>
          <p className="mt-0.5">
            Essential cookies keep you signed in. We may add optional
            analytics cookies later — your choice here controls whether
            they ever run. See our{" "}
            <Link href="/privacy" className="underline hover:text-foreground">
              Privacy Policy
            </Link>
            .
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => decide(false)}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          >
            Essential only
          </button>
          <button
            type="button"
            onClick={() => decide(true)}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Accept all
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Read-only helper for any analytics shim we add later. Returns ``false``
 * if no consent has been captured yet (the safe default — never run
 * analytics until the user has actively opted in).
 */
export function hasAnalyticsConsent(): boolean {
  return readConsent()?.analytics === true;
}
