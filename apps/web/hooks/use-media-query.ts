"use client";

import { useEffect, useState } from "react";

// SSR-safe matchMedia. The initial render returns `false` so the desktop
// branch never flashes on a mobile-first SSR pass; we sync on mount and
// then update on `change`. Components that key off this should be ready
// for one extra render after hydration.
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    setMatches(mql.matches);
    // `addEventListener('change', ...)` is the modern signature; older Safari
    // (< 14) used `addListener` but our target browsers are all on the new
    // one — skip the fallback to keep the hook small.
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [query]);

  return matches;
}

// Convenience: matches Tailwind's `md:` breakpoint (768px). Returns `true`
// on desktop, `false` on mobile.
export function useIsDesktop(): boolean {
  return useMediaQuery("(min-width: 768px)");
}
