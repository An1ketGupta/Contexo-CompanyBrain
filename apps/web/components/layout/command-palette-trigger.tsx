"use client";

import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { useCommandPalette } from "@/components/command-palette/command-palette-context";

/** Discoverability hint: a sidebar button that opens the command palette and
 *  shows the platform-correct shortcut. Mirrors the pattern Vercel/Linear use:
 *  a search-shaped affordance makes the keyboard layer findable for users who
 *  don't know it exists yet. */
export function CommandPaletteTrigger() {
  const { toggle } = useCommandPalette();
  // SSR-safe: render the Mac glyph by default and swap to Ctrl after mount.
  const [isMac, setIsMac] = useState(true);
  useEffect(() => {
    if (typeof navigator !== "undefined") {
      setIsMac(/mac/i.test(navigator.platform));
    }
  }, []);

  return (
    <div className="px-3 pb-2">
      <button
        type="button"
        onClick={toggle}
        className="group flex w-full items-center gap-2 rounded-md border border-border bg-muted/40 px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:border-foreground/20 hover:bg-muted hover:text-foreground"
      >
        <Search className="h-3.5 w-3.5 shrink-0" />
        <span className="flex-1 truncate">Search…</span>
        <kbd className="rounded border border-border bg-background px-1 font-mono text-[10px]">
          {isMac ? "⌘" : "Ctrl"}
        </kbd>
        <kbd className="rounded border border-border bg-background px-1 font-mono text-[10px]">
          K
        </kbd>
      </button>
    </div>
  );
}
