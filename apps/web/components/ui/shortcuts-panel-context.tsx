"use client";

import { createContext, useContext, useMemo, useState } from "react";

interface ShortcutsPanelState {
  open: boolean;
  setOpen: (open: boolean) => void;
}

const ShortcutsPanelContext = createContext<ShortcutsPanelState | null>(null);

export function ShortcutsPanelProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const value = useMemo(() => ({ open, setOpen }), [open]);
  return (
    <ShortcutsPanelContext.Provider value={value}>
      {children}
    </ShortcutsPanelContext.Provider>
  );
}

export function useShortcutsPanel(): ShortcutsPanelState {
  const ctx = useContext(ShortcutsPanelContext);
  if (!ctx) return { open: false, setOpen: () => {} };
  return ctx;
}
