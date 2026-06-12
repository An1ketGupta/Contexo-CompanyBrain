"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

interface CommandPaletteState {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
}

const CommandPaletteContext = createContext<CommandPaletteState | null>(null);

/** Lightweight wrapper around a single `open` boolean. Lives at the dashboard
 *  layout level so any descendant (sidebar, chat page, document table) can
 *  open or close the palette without prop drilling. */
export function CommandPaletteProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  // Cmd/Ctrl+K is the universally-expected shortcut for command palettes. We
  // bind it here (not in useKeyboardShortcuts) so it works on EVERY page
  // including the documents and settings routes where the chat-page shortcuts
  // hook isn't mounted.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isMac = typeof navigator !== "undefined" && /mac/i.test(navigator.platform);
      const meta = isMac ? e.metaKey : e.ctrlKey;
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggle]);

  const value = useMemo(() => ({ open, setOpen, toggle }), [open, toggle]);
  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
    </CommandPaletteContext.Provider>
  );
}

export function useCommandPalette(): CommandPaletteState {
  const ctx = useContext(CommandPaletteContext);
  if (!ctx) {
    // Render-safe fallback: any component outside the dashboard tree (e.g.
    // auth pages) gets a no-op so calling `toggle()` doesn't crash the render.
    return { open: false, setOpen: () => {}, toggle: () => {} };
  }
  return ctx;
}
